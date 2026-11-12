"""Contract layer: parse the contract JSON into typed dataclasses.

This is purely structural validation (shape, required keys, obvious type checks).
Anything that needs the semantic model — does this metric exist, does this filter's
dataset make sense, is there a join path — happens in plan.py, because that's where
the model and the contract meet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import InvalidContractError

_SUPPORTED_OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "not_in")
_SUPPORTED_COMPARE_OUTPUTS = ("values", "delta", "pct_change")


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: Any
    dataset_hint: str | None  # the contract's own "model" key, if given; cross-checked later


@dataclass(frozen=True)
class MetricRequest:
    name: str
    alias: str
    filters: tuple[Filter, ...]


@dataclass(frozen=True)
class CompareSpec:
    dimension: str
    periods: tuple[str, ...]
    primary: str
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class Contract:
    metrics: tuple[MetricRequest, ...]
    group_by: tuple[str, ...]
    filters: tuple[Filter, ...]
    compare: CompareSpec | None
    totals: str | None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidContractError(message)


def _parse_filter(entry: Any, where: str) -> Filter:
    _require(isinstance(entry, dict), f"{where} must be an object")
    field_name = entry.get("field")
    op = entry.get("op")
    _require(isinstance(field_name, str) and field_name, f"{where}.field must be a non-empty string")
    _require("value" in entry, f"{where}.value is required")
    _require(op in _SUPPORTED_OPS, f"{where}.op must be one of {_SUPPORTED_OPS}, got {op!r}")
    dataset_hint = entry.get("model")
    _require(
        dataset_hint is None or isinstance(dataset_hint, str),
        f"{where}.model must be a string if present",
    )
    return Filter(field=field_name, op=op, value=entry.get("value"), dataset_hint=dataset_hint)


def _parse_metric(entry: Any, index: int) -> MetricRequest:
    where = f"contract.metrics[{index}]"
    _require(isinstance(entry, dict), f"{where} must be an object")
    name = entry.get("name")
    _require(isinstance(name, str) and name, f"{where}.name must be a non-empty string")
    alias = entry.get("as", name)
    _require(isinstance(alias, str) and alias, f"{where}.as must be a non-empty string if present")
    raw_filters = entry.get("filters", [])
    _require(isinstance(raw_filters, list), f"{where}.filters must be a list")
    filters = tuple(_parse_filter(f, f"{where}.filters[{i}]") for i, f in enumerate(raw_filters))
    return MetricRequest(name=name, alias=alias, filters=filters)


def _parse_compare(entry: Any) -> CompareSpec:
    where = "contract.compare"
    _require(isinstance(entry, dict), f"{where} must be an object")
    dimension = entry.get("dimension")
    periods = entry.get("periods")
    primary = entry.get("primary")
    outputs = entry.get("outputs", ["values"])
    _require(isinstance(dimension, str) and dimension, f"{where}.dimension must be a non-empty string")
    _require(
        isinstance(periods, list) and len(periods) > 0,
        f"{where}.periods must be a non-empty list",
    )
    for i, p in enumerate(periods):
        _require(isinstance(p, (str, int)), f"{where}.periods[{i}] must be a string or integer")
    _require(primary is not None, f"{where}.primary is required")
    _require(isinstance(outputs, list) and len(outputs) > 0, f"{where}.outputs must be a non-empty list")
    for i, o in enumerate(outputs):
        _require(
            o in _SUPPORTED_COMPARE_OUTPUTS,
            f"{where}.outputs[{i}] must be one of {_SUPPORTED_COMPARE_OUTPUTS}, got {o!r}",
        )
    return CompareSpec(
        dimension=dimension,
        periods=tuple(str(p) for p in periods),
        primary=str(primary),
        outputs=tuple(outputs),
    )


def parse_contract(raw: dict) -> Contract:
    _require(isinstance(raw, dict), "contract must be a JSON object")

    raw_metrics = raw.get("metrics")
    _require(
        isinstance(raw_metrics, list) and len(raw_metrics) > 0,
        "contract.metrics must be a non-empty list",
    )
    metrics = tuple(_parse_metric(m, i) for i, m in enumerate(raw_metrics))

    seen_aliases: set[str] = set()
    for m in metrics:
        _require(m.alias not in seen_aliases, f"contract.metrics has a duplicate output alias: {m.alias!r}")
        seen_aliases.add(m.alias)

    raw_group_by = raw.get("group_by", [])
    _require(isinstance(raw_group_by, list), "contract.group_by must be a list")
    for i, g in enumerate(raw_group_by):
        _require(isinstance(g, str) and g, f"contract.group_by[{i}] must be a non-empty string")
    group_by = tuple(raw_group_by)

    raw_filters = raw.get("filters", [])
    _require(isinstance(raw_filters, list), "contract.filters must be a list")
    filters = tuple(_parse_filter(f, f"contract.filters[{i}]") for i, f in enumerate(raw_filters))

    raw_compare = raw.get("compare")
    compare = _parse_compare(raw_compare) if raw_compare is not None else None

    totals = raw.get("totals")
    _require(totals is None or isinstance(totals, str), "contract.totals must be a string if present")

    return Contract(metrics=metrics, group_by=group_by, filters=filters, compare=compare, totals=totals)
