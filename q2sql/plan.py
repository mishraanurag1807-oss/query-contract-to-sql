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
from .model import SemanticModel

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
    dataset: str
    column: str
    output_name: str


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
class GroupByColumn:
    """A group_by dimension: `projected` is its flattened name inside `base`
    (possibly namespaced to avoid collisions); `display_name` is the dimension's
    own name from the contract/model, which is what the final output column is
    called.
    """

    projected: ProjectedColumn
    display_name: str


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
    """Collects everything build_plan needs, keeping dataset/dimension resolution
    and join-path discovery in one deterministic, order-preserving pass.
    """

    def __init__(self, model: SemanticModel, contract: Contract):
        self.model = model
        self.contract = contract
        self.anchor = self._resolve_anchor()
        self._joins: list[JoinStep] = []
        self._joined_datasets: set[str] = {self.anchor}
        self._projected: dict[str, ProjectedColumn] = {}  # output_name -> column

    # ---- dataset / dimension / metric resolution -----------------------------

    def _resolve_anchor(self) -> str:
        datasets_used = []
        for m in self.contract.metrics:
            metric = self.model.metrics.get(m.name)
            if metric is None:
                raise UnknownMetricError(
                    f"contract.metrics names unknown metric {m.name!r}; "
                    f"declared metrics are {sorted(self.model.metrics)}"
                )
            if metric.dataset not in datasets_used:
                datasets_used.append(metric.dataset)
        if len(datasets_used) > 1:
            raise UnsupportedContractError(
                "contract.metrics reference more than one dataset "
                f"({datasets_used}); a single contract spanning multiple fact tables "
                "is not supported yet (would require aggregating each fact separately "
                "at a shared dimension grain before joining)"
            )
        return datasets_used[0]

    def _resolve_dimension(self, name: str, where: str) -> tuple[str, str]:
        dim = self.model.dimensions.get(name)
        if dim is None:
            raise UnknownDimensionError(
                f"{where} names unknown dimension {name!r}; "
                f"declared dimensions are {sorted(self.model.dimensions)}"
            )
        return dim.dataset, dim.column

    def _ensure_joined(self, dataset: str, where: str) -> None:
        if dataset in self._joined_datasets:
            return
        path = find_join_path(self.model, self.anchor, dataset)
        for step in path:
            if step.dataset not in self._joined_datasets:
                self._joins.append(step)
                self._joined_datasets.add(step.dataset)

    def _project(self, dataset: str, column: str) -> str:
        """Ensure (dataset, column) is projected by the base CTE; return its flat
        output name. Idempotent: the same (dataset, column) always maps to the
        same output name, so repeated references don't duplicate projections.
        """
        output_name = column if dataset == self.anchor else f"{dataset}__{column}"
        key = f"{dataset}.{column}"
        existing = self._projected.get(key)
        if existing is not None:
            return existing.output_name
        # Guard against two different (dataset, column) pairs wanting the same
        # flat name (only possible for anchor-dataset collisions in principle;
        # kept as a defensive check rather than a silent overwrite).
        for other_key, other in self._projected.items():
            if other.output_name == output_name and other_key != key:
                raise UnsupportedContractError(
                    f"internal projection name clash for {output_name!r} "
                    f"between {other_key!r} and {key!r}"
                )
        self._projected[key] = ProjectedColumn(dataset=dataset, column=column, output_name=output_name)
        return output_name

    def resolve_dimension_and_project(self, name: str, where: str) -> ProjectedColumn:
        dataset, column = self._resolve_dimension(name, where)
        self._ensure_joined(dataset, where)
        output_name = self._project(dataset, column)
        return ProjectedColumn(dataset=dataset, column=column, output_name=output_name)

    def project_metric_source(self, dataset: str, column: str) -> str:
        self._ensure_joined(dataset, f"metric expression {column!r}")
        return self._project(dataset, column)

    @property
    def projected_columns(self) -> tuple[ProjectedColumn, ...]:
        return tuple(self._projected.values())

    @property
    def joins(self) -> tuple[JoinStep, ...]:
        return tuple(self._joins)


def _validate_filter_value(f: Filter, where: str) -> Any:
    value = coerce_literal_value(f.value)
    if f.op in _LIST_OPS:
        if not isinstance(f.value, list) or len(f.value) == 0:
            raise InvalidContractError(f"{where}: op {f.op!r} requires a non-empty list value")
        return tuple(coerce_literal_value(v) for v in f.value)
    if f.op in _SCALAR_OPS:
        if isinstance(f.value, list):
            raise InvalidContractError(f"{where}: op {f.op!r} does not take a list value")
        return value
    raise UnsupportedContractError(f"{where}: operator {f.op!r} is not supported")


def _check_filter_dataset_hint(f: Filter, declared_dataset: str, where: str) -> None:
    if f.dataset_hint is not None and f.dataset_hint != declared_dataset:
        raise InvalidContractError(
            f"{where}: filter says model={f.dataset_hint!r} but {f.field!r} is declared "
            f"on {declared_dataset!r} in the semantic model"
        )


def _build_base_condition(builder: _PlanBuilder, f: Filter, where: str) -> BaseCondition:
    dataset, column = builder._resolve_dimension(f.field, where)
    _check_filter_dataset_hint(f, dataset, where)
    output_name = builder.resolve_dimension_and_project(f.field, where).output_name
    value = _validate_filter_value(f, where)
    return BaseCondition(column=output_name, op=f.op, value=value)


def _build_raw_condition_for_dimension(
    builder: _PlanBuilder, field: str, op: str, raw_value: Any, where: str
) -> RawCondition:
    dataset, column = builder._resolve_dimension(field, where)
    builder._ensure_joined(dataset, where)
    value = raw_value
    if op in _LIST_OPS:
        value = tuple(coerce_literal_value(v) for v in raw_value)
    else:
        value = coerce_literal_value(raw_value)
    return RawCondition(dataset=dataset, column=column, op=op, value=value)


def _build_metric_output(builder: _PlanBuilder, m: MetricRequest) -> MetricOutput:
    metric = builder.model.metrics.get(m.name)
    if metric is None:
        raise UnknownMetricError(
            f"contract metric {m.name!r} is not declared in the semantic model; "
            f"declared metrics are {sorted(builder.model.metrics)}"
        )
    source_column = builder.project_metric_source(metric.dataset, metric.expression)
    conditions = tuple(
        _build_base_condition(builder, f, f"metric {m.alias!r} filters") for f in m.filters
    )
    return MetricOutput(alias=m.alias, agg=metric.agg, source_column=source_column, conditions=conditions)


def _build_compare_plan(
    builder: _PlanBuilder, compare: CompareSpec, metrics: tuple[MetricRequest, ...]
) -> tuple[ComparePlan, list[RawCondition]]:
    where = "contract.compare"
    dataset, column = builder._resolve_dimension(compare.dimension, f"{where}.dimension")
    builder._ensure_joined(dataset, where)
    dim_output_name = builder._project(dataset, column)

    periods = tuple(coerce_literal_value(p) for p in compare.periods)
    primary = coerce_literal_value(compare.primary)
    if primary not in periods:
        raise InvalidContractError(
            f"{where}.primary ({compare.primary!r}) must be one of {where}.periods {compare.periods!r}"
        )
    others = [p for p in periods if p != primary]
    needs_delta = "delta" in compare.outputs
    needs_pct = "pct_change" in compare.outputs
    if (needs_delta or needs_pct) and len(periods) != 2:
        raise UnsupportedContractError(
            f"{where}: delta/pct_change require exactly 2 periods, got {len(periods)} ({periods!r})"
        )

    pivots: list[ComparePivot] = []
    derived: list[CompareDerived] = []
    value_aliases: list[str] = []
    want_values = "values" in compare.outputs

    for m in metrics:
        metric = builder.model.metrics.get(m.name)
        if metric is None:
            raise UnknownMetricError(
                f"contract metric {m.name!r} is not declared in the semantic model; "
                f"declared metrics are {sorted(builder.model.metrics)}"
            )
        source_column = builder.project_metric_source(metric.dataset, metric.expression)
        metric_filter_conditions = tuple(
            _build_base_condition(builder, f, f"metric {m.alias!r} filters") for f in m.filters
        )
        period_columns: dict[Any, str] = {}
        for period in periods:
            col_alias = f"{m.alias}_{period}"
            conditions = (BaseCondition(column=dim_output_name, op="=", value=period),) + metric_filter_conditions
            pivots.append(
                ComparePivot(column_alias=col_alias, agg=metric.agg, source_column=source_column, conditions=conditions)
            )
            period_columns[period] = col_alias
            if want_values:
                value_aliases.append(col_alias)

        if needs_delta or needs_pct:
            other = others[0]
            primary_col = period_columns[primary]
            other_col = period_columns[other]
            if needs_delta:
                derived.append(
                    CompareDerived(alias=f"{m.alias}_delta", kind="delta", primary_column=primary_col, other_column=other_col)
                )
            if needs_pct:
                derived.append(
                    CompareDerived(
                        alias=f"{m.alias}_pct_change", kind="pct_change", primary_column=primary_col, other_column=other_col
                    )
                )

    compare_plan = ComparePlan(pivots=tuple(pivots), value_column_aliases=tuple(value_aliases), derived=tuple(derived))

    period_filter = _build_raw_condition_for_dimension(builder, compare.dimension, "in", list(periods), where)
    return compare_plan, [period_filter]


def build_plan(model: SemanticModel, contract: Contract) -> LogicalPlan:
    builder = _PlanBuilder(model, contract)

    group_by_cols = [
        GroupByColumn(
            projected=builder.resolve_dimension_and_project(name, f"contract.group_by[{i}]"),
            display_name=name,
        )
        for i, name in enumerate(contract.group_by)
    ]

    raw_where: list[RawCondition] = [
        _build_raw_condition_for_dimension(builder, f.field, f.op, f.value, f"contract.filters[{i}] ({f.field!r})")
        for i, f in enumerate(contract.filters)
    ]

    compare_plan: ComparePlan | None = None
    metrics: tuple[MetricOutput, ...] = ()
    if contract.compare is not None:
        compare_plan, extra_where = _build_compare_plan(builder, contract.compare, contract.metrics)
        raw_where.extend(extra_where)
    else:
        metrics = tuple(_build_metric_output(builder, m) for m in contract.metrics)

    totals = contract.totals
    if totals is not None:
        if totals != "grand":
            raise UnsupportedContractError(f"contract.totals value {totals!r} is not supported (only 'grand' is)")
        if not group_by_cols:
            raise UnsupportedContractError("contract.totals requires at least one contract.group_by dimension")

    return LogicalPlan(
        anchor=builder.anchor,
        joins=builder.joins,
        base_projected_columns=builder.projected_columns,
        base_where=tuple(raw_where),
        group_by=tuple(group_by_cols),
        metrics=metrics,
        compare=compare_plan,
        totals=totals,
    )
