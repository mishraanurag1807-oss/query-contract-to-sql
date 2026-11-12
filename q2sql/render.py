"""Rendering: turn a LogicalPlan into a SQL string through a Dialect.

Every contract shape goes through the same three-stage shape, which is what
keeps this module small and uniform instead of one branch per contract:

  WITH base AS (<joins + row-level filters, flattened to simple column names>)
  SELECT ...        -- "combined": the grouped aggregate, UNION ALL'd with a
  FROM (  ... )        totals aggregate over the same base when totals="grand"
  SELECT ...        -- the final projection: for a plain contract this is a
  FROM (combined)      pass-through; for `compare` it's where delta/pct_change
  [ORDER BY ...]       get computed, from combined's own columns, never summed.

Contract A has no group_by/compare/totals, so stages 2 collapses to one plain
SELECT and stage 3 is a pass-through projection — same code path, fewer parts.
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

    base_sql = _render_base_select(plan, dialect)

    core_sql = _render_aggregate_select(plan, dialect, is_totals=False, sort_key=(0 if has_totals else None))
    if has_totals:
        totals_sql = _render_aggregate_select(plan, dialect, is_totals=True, sort_key=1)
        combined_sql = core_sql + "\nUNION ALL\n" + totals_sql
    else:
        combined_sql = core_sql

    outer_sql = _render_outer_select(plan, dialect, combined_sql, has_totals)

    return f"WITH {q(BASE_CTE_NAME)} AS (\n{_indent(base_sql)}\n)\n{outer_sql}"


def _indent(text: str, by: int = 2) -> str:
    pad = " " * by
    return "\n".join(pad + line if line else line for line in text.split("\n"))


def _render_base_select(plan: LogicalPlan, dialect: Dialect) -> str:
    q = dialect.quote_ident
    select_items = [
        f"{q(col.dataset)}.{q(col.column)} AS {q(col.output_name)}" for col in plan.base_projected_columns
    ]
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
    column_ref = f"{q(c.dataset)}.{q(c.column)}"
    return f"{column_ref} {dialect.op_sql(c.op)} {dialect.render_value_or_list(c.op, c.value)}"


def _render_base_condition(c: BaseCondition, dialect: Dialect) -> str:
    q = dialect.quote_ident
    return f"{q(c.column)} {dialect.op_sql(c.op)} {dialect.render_value_or_list(c.op, c.value)}"


def _render_metric_agg_expr(agg: str, source_column: str, conditions: tuple, dialect: Dialect) -> str:
    q = dialect.quote_ident
    col_ref = q(source_column)
    if conditions:
        cond_sql = " AND ".join(_render_base_condition(c, dialect) for c in conditions)
        inner = f"CASE WHEN {cond_sql} THEN {col_ref} END"
    else:
        inner = col_ref
    if agg == "sum":
        return f"SUM({inner})"
    if agg == "count_distinct":
        return f"COUNT(DISTINCT {inner})"
    raise ValueError(f"unreachable: unknown agg {agg!r}")  # model.py already restricts this


def _render_aggregate_select(plan: LogicalPlan, dialect: Dialect, *, is_totals: bool, sort_key: int | None) -> str:
    q = dialect.quote_ident
    select_items = []
    for col in plan.group_by:
        if is_totals:
            select_items.append(f"{dialect.render_literal(TOTALS_LABEL)} AS {q(col.display_name)}")
        else:
            select_items.append(f"{q(col.projected.output_name)} AS {q(col.display_name)}")

    if plan.compare is not None:
        for pivot in plan.compare.pivots:
            expr = _render_metric_agg_expr(pivot.agg, pivot.source_column, pivot.conditions, dialect)
            select_items.append(f"{expr} AS {q(pivot.column_alias)}")
    else:
        for metric in plan.metrics:
            expr = _render_metric_agg_expr(metric.agg, metric.source_column, metric.conditions, dialect)
            select_items.append(f"{expr} AS {q(metric.alias)}")

    if sort_key is not None:
        select_items.append(f"{sort_key} AS {q(SORT_KEY_COLUMN)}")

    select_clause = "SELECT\n" + ",\n".join(_indent(item) for item in select_items)
    lines = [select_clause, f"FROM {q(BASE_CTE_NAME)}"]
    if not is_totals and plan.group_by:
        group_cols_sql = ", ".join(q(col.projected.output_name) for col in plan.group_by)
        lines.append(f"GROUP BY {group_cols_sql}")
    return "\n".join(lines)


def _render_outer_select(plan: LogicalPlan, dialect: Dialect, combined_sql: str, has_totals: bool) -> str:
    q = dialect.quote_ident
    select_items = [q(col.display_name) for col in plan.group_by]

    if plan.compare is not None:
        for alias in plan.compare.value_column_aliases:
            select_items.append(q(alias))
        for d in plan.compare.derived:
            primary_ref = q(d.primary_column)
            other_ref = q(d.other_column)
            if d.kind == "delta":
                expr = f"({primary_ref} - {other_ref})"
            else:
                expr = dialect.safe_divide(f"({primary_ref} - {other_ref})", other_ref)
            select_items.append(f"{expr} AS {q(d.alias)}")
    else:
        for metric in plan.metrics:
            select_items.append(q(metric.alias))

    select_clause = "SELECT " + ", ".join(select_items)
    from_clause = f"FROM (\n{_indent(combined_sql)}\n) AS {q(COMBINED_NAME)}"

    order_items = []
    if has_totals:
        order_items.append(q(SORT_KEY_COLUMN))
    order_items.extend(q(col.display_name) for col in plan.group_by)

    parts = [select_clause, from_clause]
    if order_items:
        parts.append(f"ORDER BY {', '.join(order_items)}")
    return "\n".join(parts)
