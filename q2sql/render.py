"""Rendering: turn a LogicalPlan into a SQL string through a Dialect.

Every contract shape goes through the same shape of query:

  WITH base AS (<joins + row-level filters, flattened to simple column names>)
  SELECT ...   -- the aggregate: grouped, and pivoted into period columns when
  FROM base       `compare` is present. GROUP BY if there's a group_by.

Two contract features make that single SELECT not enough on its own, and each
adds one more layer around it, never changing the layer below:

  - `totals="grand"` needs a second, ungrouped aggregate over the same base,
    UNION ALL'd on with a sort key so it reliably sorts last.
  - `compare` needs delta/pct_change computed from two already-aggregated
    columns, which has to happen in a SELECT *on top of* the aggregate, never
    summed from group rows.

Contract A has neither, so it renders as just the one SELECT — same function,
fewer layers used, not a special case.
"""

from __future__ import annotations

from .dialect import Dialect
from .plan import BaseCondition, LogicalPlan, RawCondition, SORT_KEY_COLUMN

BASE_CTE_NAME = "base"
COMBINED_NAME = "combined"
TOTALS_LABEL = "Total"


def render(plan: LogicalPlan, dialect: Dialect) -> str:
    q = dialect.quote_ident
    has_totals = plan.totals == "grand"
    base_cte = f"WITH {q(BASE_CTE_NAME)} AS (\n{_indent(_render_base_select(plan, dialect))}\n)"

    aggregate_sql = _render_aggregate_select(plan, dialect, is_totals=False, sort_key=(0 if has_totals else None))

    if plan.compare is None and not has_totals:
        # Nothing needs to look at more than one row of the aggregate (no
        # delta/pct_change, no totals row to sort/hide), so the aggregate
        # select already has its final shape.
        if plan.group_by:
            aggregate_sql += f"\nORDER BY {', '.join(q(c.display_name) for c in plan.group_by)}"
        return f"{base_cte}\n{aggregate_sql}"

    if has_totals:
        totals_sql = _render_aggregate_select(plan, dialect, is_totals=True, sort_key=1)
        combined_sql = f"{aggregate_sql}\nUNION ALL\n{totals_sql}"
    else:
        combined_sql = aggregate_sql

    outer_sql = _render_outer_select(plan, dialect, combined_sql, has_totals)
    return f"{base_cte}\n{outer_sql}"


def _indent(text: str, by: int = 2) -> str:
    pad = " " * by
    return "\n".join(pad + line if line else line for line in text.split("\n"))


def _render_base_select(plan: LogicalPlan, dialect: Dialect) -> str:
    q = dialect.quote_ident
    select_items = [f"{q(col.dataset)}.{q(col.column)} AS {q(col.name)}" for col in plan.base_projected_columns]
    select_clause = "SELECT\n" + ",\n".join(_indent(item) for item in select_items)
    from_clause = f"FROM {q(plan.anchor)}"

    join_lines = [
        f"LEFT JOIN {q(step.dataset)} ON {q(step.from_dataset)}.{q(step.from_column)} = {q(step.dataset)}.{q(step.to_column)}"
        for step in plan.joins
    ]

    parts = [select_clause, from_clause, *join_lines]
    if plan.base_where:
        conds = " AND ".join(_render_raw_condition(c, dialect) for c in plan.base_where)
        parts.append(f"WHERE {conds}")
    return "\n".join(parts)


def _render_raw_condition(c: RawCondition, dialect: Dialect) -> str:
    q = dialect.quote_ident
    return f"{q(c.dataset)}.{q(c.column)} {dialect.op_sql(c.op)} {dialect.render_value_or_list(c.op, c.value)}"


def _render_base_condition(c: BaseCondition, dialect: Dialect) -> str:
    q = dialect.quote_ident
    return f"{q(c.column)} {dialect.op_sql(c.op)} {dialect.render_value_or_list(c.op, c.value)}"


def _render_metric_agg_expr(agg: str, source_column: str, conditions: tuple, dialect: Dialect) -> str:
    col_ref = dialect.quote_ident(source_column)
    if conditions:
        cond_sql = " AND ".join(_render_base_condition(c, dialect) for c in conditions)
        col_ref = f"CASE WHEN {cond_sql} THEN {col_ref} END"
    if agg == "sum":
        return f"SUM({col_ref})"
    if agg == "count_distinct":
        return f"COUNT(DISTINCT {col_ref})"
    raise ValueError(f"unreachable: unknown agg {agg!r}")  # model.py already restricts this


def _render_aggregate_select(plan: LogicalPlan, dialect: Dialect, *, is_totals: bool, sort_key: int | None) -> str:
    q = dialect.quote_ident
    select_items = []
    for col in plan.group_by:
        source = dialect.render_literal(TOTALS_LABEL) if is_totals else q(col.column)
        select_items.append(f"{source} AS {q(col.display_name)}")

    outputs = plan.compare.pivots if plan.compare is not None else plan.metrics
    for out in outputs:
        alias = out.column_alias if plan.compare is not None else out.alias
        expr = _render_metric_agg_expr(out.agg, out.source_column, out.conditions, dialect)
        select_items.append(f"{expr} AS {q(alias)}")

    if sort_key is not None:
        select_items.append(f"{sort_key} AS {q(SORT_KEY_COLUMN)}")

    select_clause = "SELECT\n" + ",\n".join(_indent(item) for item in select_items)
    lines = [select_clause, f"FROM {q(BASE_CTE_NAME)}"]
    if not is_totals and plan.group_by:
        lines.append(f"GROUP BY {', '.join(q(c.column) for c in plan.group_by)}")
    return "\n".join(lines)


def _render_outer_select(plan: LogicalPlan, dialect: Dialect, combined_sql: str, has_totals: bool) -> str:
    q = dialect.quote_ident
    select_items = [q(c.display_name) for c in plan.group_by]

    if plan.compare is not None:
        select_items.extend(q(alias) for alias in plan.compare.value_column_aliases)
        for d in plan.compare.derived:
            primary_ref, other_ref = q(d.primary_column), q(d.other_column)
            if d.kind == "delta":
                expr = f"({primary_ref} - {other_ref})"
            else:
                expr = dialect.safe_divide(f"({primary_ref} - {other_ref})", other_ref)
            select_items.append(f"{expr} AS {q(d.alias)}")
    else:
        select_items.extend(q(m.alias) for m in plan.metrics)

    select_clause = "SELECT " + ", ".join(select_items)
    from_clause = f"FROM (\n{_indent(combined_sql)}\n) AS {q(COMBINED_NAME)}"

    order_items = [q(SORT_KEY_COLUMN)] if has_totals else []
    order_items.extend(q(c.display_name) for c in plan.group_by)

    parts = [select_clause, from_clause]
    if order_items:
        parts.append(f"ORDER BY {', '.join(order_items)}")
    return "\n".join(parts)
