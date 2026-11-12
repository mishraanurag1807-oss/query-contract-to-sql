"""Planning: turn a validated (contract, model) pair into a LogicalPlan.

The plan is a dialect-independent intermediate structure. Everything that needs
judgement — which joins are needed, how a filter's dataset is resolved, how
periods get typed, how totals are expressed — happens here, once. render.py then
turns a LogicalPlan into SQL text through a Dialect, and never has to make any of
these decisions itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .contract import CompareSpec, Contract, Filter, MetricRequest
from .errors import (
    InvalidContractError,
    UnknownDimensionError,
    UnknownMetricError,
    UnsupportedContractError,
)
from .joins import JoinStep, find_join_path
from .model import Metric, SemanticModel

_DIGIT_STRING = re.compile(r"^-?\d+$")
_SCALAR_OPS = ("=", "!=", "<", "<=", ">", ">=")
_LIST_OPS = ("in", "not_in")
SORT_KEY_COLUMN = "__sort_key"


def coerce_literal_value(value: Any) -> Any:
    """The one typing rule for untyped contract values: a string made only of
    digits (with an optional leading '-') becomes an integer literal. Everything
    else passes through unchanged. Applied uniformly to filter values and compare
    periods/primary, so there is exactly one rule, not one per call site.
    """
    if isinstance(value, str) and _DIGIT_STRING.match(value):
        return int(value)
    return value


@dataclass(frozen=True)
class ProjectedColumn:
    """One column the `base` CTE exposes, under its own bare column name."""

    dataset: str
    column: str
    name: str


@dataclass(frozen=True)
class GroupByColumn:
    """A group_by dimension: `column` is its flat name inside `base` (normally the
    same as the dimension's declared name, but a dimension may map to a
    differently-named physical column); `display_name` is always the dimension's
    declared name, which is what the output column is called.
    """

    column: str
    display_name: str


@dataclass(frozen=True)
class RawCondition:
    """A condition evaluated inside the base CTE's WHERE, against a joined table."""

    dataset: str
    column: str
    op: str
    value: Any


@dataclass(frozen=True)
class BaseCondition:
    """A condition evaluated against an already-flattened `base` column."""

    column: str
    op: str
    value: Any


@dataclass(frozen=True)
class MetricOutput:
    alias: str
    agg: str
    source_column: str
    conditions: tuple[BaseCondition, ...]


@dataclass(frozen=True)
class ComparePivot:
    column_alias: str
    agg: str
    source_column: str
    conditions: tuple[BaseCondition, ...]


@dataclass(frozen=True)
class CompareDerived:
    alias: str
    kind: str  # "delta" | "pct_change"
    primary_column: str
    other_column: str


@dataclass(frozen=True)
class ComparePlan:
    pivots: tuple[ComparePivot, ...]
    value_column_aliases: tuple[str, ...]
    derived: tuple[CompareDerived, ...]


@dataclass(frozen=True)
class LogicalPlan:
    anchor: str
    joins: tuple[JoinStep, ...]
    base_projected_columns: tuple[ProjectedColumn, ...]
    base_where: tuple[RawCondition, ...]
    group_by: tuple[GroupByColumn, ...]
    metrics: tuple[MetricOutput, ...]
    compare: ComparePlan | None
    totals: str | None


class _PlanBuilder:
    """Collects everything build_plan needs: dataset/dimension/metric resolution
    against the model, join-path discovery, and the set of columns `base` must
    project — all in one deterministic, order-preserving pass over the contract.
    """

    def __init__(self, model: SemanticModel, contract: Contract):
        self.model = model
        self.anchor = self._resolve_anchor(contract)
        self._joins: list[JoinStep] = []
        self._joined_datasets: set[str] = {self.anchor}
        self._projected: dict[str, ProjectedColumn] = {}  # flat name -> column

    def _resolve_anchor(self, contract: Contract) -> str:
        datasets_used = []
        for m in contract.metrics:
            dataset = self.get_metric(m.name).dataset
            if dataset not in datasets_used:
                datasets_used.append(dataset)
        if len(datasets_used) > 1:
            raise UnsupportedContractError(
                "contract.metrics reference more than one dataset "
                f"({datasets_used}); a single contract spanning multiple fact tables "
                "is not supported yet (would require aggregating each fact separately "
                "at a shared dimension grain before joining)"
            )
        return datasets_used[0]

    def get_metric(self, name: str) -> Metric:
        metric = self.model.metrics.get(name)
        if metric is None:
            raise UnknownMetricError(
                f"contract names unknown metric {name!r}; declared metrics are {sorted(self.model.metrics)}"
            )
        return metric

    def resolve_dimension(self, name: str, where: str) -> tuple[str, str]:
        dim = self.model.dimensions.get(name)
        if dim is None:
            raise UnknownDimensionError(
                f"{where} names unknown dimension {name!r}; "
                f"declared dimensions are {sorted(self.model.dimensions)}"
            )
        return dim.dataset, dim.column

    def ensure_joined(self, dataset: str, where: str) -> None:
        if dataset in self._joined_datasets:
            return
        for step in find_join_path(self.model, self.anchor, dataset):
            if step.dataset not in self._joined_datasets:
                self._joins.append(step)
                self._joined_datasets.add(step.dataset)

    def project(self, dataset: str, column: str) -> str:
        """Ensure (dataset, column) is projected by `base`, under its own bare
        column name; idempotent. Raises if that name is already taken by a
        *different* (dataset, column) — e.g. two joined tables both having a
        same-named column — rather than silently colliding two columns into one.
        """
        existing = self._projected.get(column)
        if existing is None:
            self._projected[column] = ProjectedColumn(dataset=dataset, column=column, name=column)
        elif (existing.dataset, existing.column) != (dataset, column):
            raise UnsupportedContractError(
                f"both {existing.dataset}.{existing.column} and {dataset}.{column} "
                f"would need the base column name {column!r} — two joined tables "
                "have a same-named column this generator can't disambiguate"
            )
        return column

    def project_dimension(self, name: str, where: str) -> str:
        dataset, column = self.resolve_dimension(name, where)
        self.ensure_joined(dataset, where)
        return self.project(dataset, column)

    def project_metric(self, metric: Metric) -> str:
        self.ensure_joined(metric.dataset, f"metric {metric.name!r}")
        return self.project(metric.dataset, metric.expression)

    @property
    def projected_columns(self) -> tuple[ProjectedColumn, ...]:
        return tuple(self._projected.values())

    @property
    def joins(self) -> tuple[JoinStep, ...]:
        return tuple(self._joins)


def _coerce_filter_value(f: Filter, where: str) -> Any:
    if f.op in _LIST_OPS:
        if not isinstance(f.value, list) or len(f.value) == 0:
            raise InvalidContractError(f"{where}: op {f.op!r} requires a non-empty list value")
        return tuple(coerce_literal_value(v) for v in f.value)
    if f.op in _SCALAR_OPS:
        if isinstance(f.value, list):
            raise InvalidContractError(f"{where}: op {f.op!r} does not take a list value")
        return coerce_literal_value(f.value)
    raise UnsupportedContractError(f"{where}: operator {f.op!r} is not supported")


def _check_filter_dataset_hint(f: Filter, declared_dataset: str, where: str) -> None:
    if f.dataset_hint is not None and f.dataset_hint != declared_dataset:
        raise InvalidContractError(
            f"{where}: filter says model={f.dataset_hint!r} but {f.field!r} is declared "
            f"on {declared_dataset!r} in the semantic model"
        )


def _build_base_condition(builder: _PlanBuilder, f: Filter, where: str) -> BaseCondition:
    """A per-metric filter: evaluated inside a CASE WHEN at aggregation time, so it
    references the already-flattened `base` column, not the original joined table.
    """
    dataset, _column = builder.resolve_dimension(f.field, where)
    _check_filter_dataset_hint(f, dataset, where)
    column = builder.project_dimension(f.field, where)
    return BaseCondition(column=column, op=f.op, value=_coerce_filter_value(f, where))


def _build_raw_condition(builder: _PlanBuilder, f: Filter, where: str) -> RawCondition:
    """A row-restricting filter: evaluated in the base CTE's own WHERE clause,
    where the joined tables are still in scope.
    """
    dataset, column = builder.resolve_dimension(f.field, where)
    _check_filter_dataset_hint(f, dataset, where)
    builder.ensure_joined(dataset, where)
    return RawCondition(dataset=dataset, column=column, op=f.op, value=_coerce_filter_value(f, where))


def _build_metric_output(builder: _PlanBuilder, m: MetricRequest) -> MetricOutput:
    metric = builder.get_metric(m.name)
    source_column = builder.project_metric(metric)
    conditions = tuple(_build_base_condition(builder, f, f"metric {m.alias!r} filters") for f in m.filters)
    return MetricOutput(alias=m.alias, agg=metric.agg, source_column=source_column, conditions=conditions)


def _build_compare_plan(
    builder: _PlanBuilder, compare: CompareSpec, metrics: tuple[MetricRequest, ...]
) -> tuple[ComparePlan, RawCondition]:
    where = "contract.compare"
    dataset, _column = builder.resolve_dimension(compare.dimension, f"{where}.dimension")
    dim_column = builder.project_dimension(compare.dimension, where)

    periods = tuple(coerce_literal_value(p) for p in compare.periods)
    primary = coerce_literal_value(compare.primary)
    if primary not in periods:
        raise InvalidContractError(
            f"{where}.primary ({compare.primary!r}) must be one of {where}.periods {compare.periods!r}"
        )
    other_period = next((p for p in periods if p != primary), None)
    want_values = "values" in compare.outputs
    want_delta = "delta" in compare.outputs
    want_pct = "pct_change" in compare.outputs
    if (want_delta or want_pct) and len(periods) != 2:
        raise UnsupportedContractError(
            f"{where}: delta/pct_change require exactly 2 periods, got {len(periods)} ({periods!r})"
        )

    pivots: list[ComparePivot] = []
    derived: list[CompareDerived] = []
    value_aliases: list[str] = []

    for m in metrics:
        metric = builder.get_metric(m.name)
        source_column = builder.project_metric(metric)
        metric_filters = tuple(_build_base_condition(builder, f, f"metric {m.alias!r} filters") for f in m.filters)

        column_by_period: dict[Any, str] = {}
        for period in periods:
            col_alias = f"{m.alias}_{period}"
            period_filter = BaseCondition(column=dim_column, op="=", value=period)
            pivots.append(
                ComparePivot(
                    column_alias=col_alias, agg=metric.agg, source_column=source_column,
                    conditions=(period_filter, *metric_filters),
                )
            )
            column_by_period[period] = col_alias
            if want_values:
                value_aliases.append(col_alias)

        if want_delta or want_pct:
            primary_col, other_col = column_by_period[primary], column_by_period[other_period]
            if want_delta:
                derived.append(CompareDerived(f"{m.alias}_delta", "delta", primary_col, other_col))
            if want_pct:
                derived.append(CompareDerived(f"{m.alias}_pct_change", "pct_change", primary_col, other_col))

    plan = ComparePlan(pivots=tuple(pivots), value_column_aliases=tuple(value_aliases), derived=tuple(derived))
    period_filter = RawCondition(dataset=dataset, column=_column, op="in", value=periods)
    return plan, period_filter


def build_plan(model: SemanticModel, contract: Contract) -> LogicalPlan:
    builder = _PlanBuilder(model, contract)

    group_by = tuple(
        GroupByColumn(column=builder.project_dimension(name, f"contract.group_by[{i}]"), display_name=name)
        for i, name in enumerate(contract.group_by)
    )

    base_where = [
        _build_raw_condition(builder, f, f"contract.filters[{i}] ({f.field!r})")
        for i, f in enumerate(contract.filters)
    ]

    compare_plan: ComparePlan | None = None
    metrics: tuple[MetricOutput, ...] = ()
    if contract.compare is not None:
        compare_plan, period_filter = _build_compare_plan(builder, contract.compare, contract.metrics)
        base_where.append(period_filter)
    else:
        metrics = tuple(_build_metric_output(builder, m) for m in contract.metrics)

    totals = contract.totals
    if totals is not None:
        if totals != "grand":
            raise UnsupportedContractError(f"contract.totals value {totals!r} is not supported (only 'grand' is)")
        if not group_by:
            raise UnsupportedContractError("contract.totals requires at least one contract.group_by dimension")

    return LogicalPlan(
        anchor=builder.anchor,
        joins=builder.joins,
        base_projected_columns=builder.projected_columns,
        base_where=tuple(base_where),
        group_by=group_by,
        metrics=metrics,
        compare=compare_plan,
        totals=totals,
    )
