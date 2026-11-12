"""Model layer: load, index and validate the semantic model once.

Nothing here reads the warehouse. The semantic model JSON is the only source of
truth about datasets, how they relate, and what metrics/dimensions mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import InvalidModelError

_VALID_CARDINALITIES = ("many_to_one", "one_to_one")
_VALID_AGGS = ("sum", "count_distinct")
_VALID_MEASURE_CLASSES = ("additive", "distinct_count")


@dataclass(frozen=True)
class Dataset:
    name: str
    grain: str


@dataclass(frozen=True)
class Relationship:
    from_dataset: str
    from_column: str
    to_dataset: str
    to_column: str
    cardinality: str


@dataclass(frozen=True)
class Metric:
    name: str
    agg: str
    expression: str
    dataset: str
    measure_class: str


@dataclass(frozen=True)
class Dimension:
    name: str
    dataset: str
    column: str


@dataclass(frozen=True)
class SemanticModel:
    """Indexed, validated view of the semantic model JSON.

    Dicts here are keyed by name for O(1) lookup; nothing in this package ever
    iterates these dicts in a way that affects generated SQL without first
    sorting or otherwise fixing the order explicitly (see joins.py).
    """

    datasets: dict[str, Dataset]
    relationships: tuple[Relationship, ...]
    metrics: dict[str, Metric]
    dimensions: dict[str, Dimension]

    # dataset -> list of relationships touching it, in declaration order.
    relationships_by_dataset: dict[str, tuple[Relationship, ...]] = field(
        default_factory=dict, compare=False
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidModelError(message)


def load_semantic_model(raw: dict) -> SemanticModel:
    """Parse and validate a semantic model dict, returning an indexed SemanticModel.

    Raises InvalidModelError for any structural problem: missing keys, duplicate
    names, or references to datasets/columns that were never declared.
    """
    _require(isinstance(raw, dict), "semantic model must be a JSON object")

    datasets = _load_datasets(raw)
    relationships = _load_relationships(raw, datasets)
    metrics = _load_metrics(raw, datasets)
    dimensions = _load_dimensions(raw, datasets)

    by_dataset: dict[str, list[Relationship]] = {name: [] for name in datasets}
    for rel in relationships:
        by_dataset[rel.from_dataset].append(rel)
        by_dataset[rel.to_dataset].append(rel)
    relationships_by_dataset = {k: tuple(v) for k, v in by_dataset.items()}

    return SemanticModel(
        datasets=datasets,
        relationships=relationships,
        metrics=metrics,
        dimensions=dimensions,
        relationships_by_dataset=relationships_by_dataset,
    )


def _load_datasets(raw: dict) -> dict[str, Dataset]:
    entries = raw.get("datasets")
    _require(isinstance(entries, list) and len(entries) > 0, "model.datasets must be a non-empty list")
    datasets: dict[str, Dataset] = {}
    for i, entry in enumerate(entries):
        _require(isinstance(entry, dict), f"model.datasets[{i}] must be an object")
        name = entry.get("name")
        _require(isinstance(name, str) and name, f"model.datasets[{i}].name must be a non-empty string")
        _require(name not in datasets, f"model.datasets has a duplicate name: {name!r}")
        grain = entry.get("grain", "")
        datasets[name] = Dataset(name=name, grain=grain)
    return datasets


def _load_relationships(raw: dict, datasets: dict[str, Dataset]) -> tuple[Relationship, ...]:
    entries = raw.get("relationships", [])
    _require(isinstance(entries, list), "model.relationships must be a list")
    rels: list[Relationship] = []
    for i, entry in enumerate(entries):
        where = f"model.relationships[{i}]"
        _require(isinstance(entry, dict), f"{where} must be an object")
        from_ds = entry.get("from")
        to_ds = entry.get("to")
        from_col = entry.get("from_column")
        to_col = entry.get("to_column")
        cardinality = entry.get("cardinality")
        _require(from_ds in datasets, f"{where}.from references unknown dataset {from_ds!r}")
        _require(to_ds in datasets, f"{where}.to references unknown dataset {to_ds!r}")
        _require(isinstance(from_col, str) and from_col, f"{where}.from_column must be a non-empty string")
        _require(isinstance(to_col, str) and to_col, f"{where}.to_column must be a non-empty string")
        _require(
            cardinality in _VALID_CARDINALITIES,
            f"{where}.cardinality must be one of {_VALID_CARDINALITIES}, got {cardinality!r}",
        )
        rels.append(
            Relationship(
                from_dataset=from_ds,
                from_column=from_col,
                to_dataset=to_ds,
                to_column=to_col,
                cardinality=cardinality,
            )
        )
    return tuple(rels)


def _load_metrics(raw: dict, datasets: dict[str, Dataset]) -> dict[str, Metric]:
    entries = raw.get("metrics", [])
    _require(isinstance(entries, list), "model.metrics must be a list")
    metrics: dict[str, Metric] = {}
    for i, entry in enumerate(entries):
        where = f"model.metrics[{i}]"
        _require(isinstance(entry, dict), f"{where} must be an object")
        name = entry.get("name")
        agg = entry.get("agg")
        expression = entry.get("expression")
        dataset = entry.get("model")
        measure_class = entry.get("measure_class")
        _require(isinstance(name, str) and name, f"{where}.name must be a non-empty string")
        _require(name not in metrics, f"model.metrics has a duplicate name: {name!r}")
        _require(agg in _VALID_AGGS, f"{where} ({name}).agg must be one of {_VALID_AGGS}, got {agg!r}")
        _require(
            isinstance(expression, str) and expression,
            f"{where} ({name}).expression must be a non-empty string",
        )
        _require(dataset in datasets, f"{where} ({name}).model references unknown dataset {dataset!r}")
        _require(
            measure_class in _VALID_MEASURE_CLASSES,
            f"{where} ({name}).measure_class must be one of {_VALID_MEASURE_CLASSES}, got {measure_class!r}",
        )
        metrics[name] = Metric(
            name=name, agg=agg, expression=expression, dataset=dataset, measure_class=measure_class
        )
    return metrics


def _load_dimensions(raw: dict, datasets: dict[str, Dataset]) -> dict[str, Dimension]:
    entries = raw.get("dimensions", [])
    _require(isinstance(entries, list), "model.dimensions must be a list")
    dimensions: dict[str, Dimension] = {}
    for i, entry in enumerate(entries):
        where = f"model.dimensions[{i}]"
        _require(isinstance(entry, dict), f"{where} must be an object")
        name = entry.get("name")
        dataset = entry.get("model")
        column = entry.get("column", name)
        _require(isinstance(name, str) and name, f"{where}.name must be a non-empty string")
        _require(name not in dimensions, f"model.dimensions has a duplicate name: {name!r}")
        _require(dataset in datasets, f"{where} ({name}).model references unknown dataset {dataset!r}")
        _require(isinstance(column, str) and column, f"{where} ({name}).column must be a non-empty string")
        dimensions[name] = Dimension(name=name, dataset=dataset, column=column)
    return dimensions
