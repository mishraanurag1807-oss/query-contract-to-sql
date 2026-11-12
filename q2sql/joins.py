"""Join resolution: find how to reach a dataset from the anchor through declared
relationships only. Nothing in a contract ever names a join directly.

Deterministic BFS over an undirected view of the relationship graph: each
relationship is traversable in either direction, but the join actually emitted
always attaches the new table with a LEFT JOIN (see plan.py / dialect.py), so a
fact row whose dimension row is missing still survives.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import AmbiguousJoinPathError, NoJoinPathError
from .model import Relationship, SemanticModel


@dataclass(frozen=True)
class JoinStep:
    """One LEFT JOIN: attach `dataset` onto the already-joined `from_dataset`."""

    dataset: str
    from_dataset: str
    from_column: str
    to_column: str


def _edge_sort_key(rel: Relationship, other_end: str) -> tuple:
    return (other_end, rel.from_dataset, rel.from_column, rel.to_dataset, rel.to_column)


def _neighbors(model: SemanticModel, node: str) -> list[tuple[str, Relationship]]:
    """Datasets directly reachable from `node`, sorted deterministically."""
    out = []
    for rel in model.relationships_by_dataset.get(node, ()):
        if rel.from_dataset == node:
            out.append((rel.to_dataset, rel))
        else:
            out.append((rel.from_dataset, rel))
    out.sort(key=lambda pair: _edge_sort_key(pair[1], pair[0]))
    return out


def _shortest_distances(model: SemanticModel, anchor: str) -> dict[str, int]:
    distances = {anchor: 0}
    frontier = [anchor]
    while frontier:
        next_frontier = []
        for node in frontier:
            for neighbor, _rel in _neighbors(model, node):
                if neighbor not in distances:
                    distances[neighbor] = distances[node] + 1
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return distances


def _enumerate_shortest_paths(
    model: SemanticModel, anchor: str, target: str, distances: dict[str, int]
) -> list[tuple[Relationship, ...]]:
    """All simple paths anchor -> target whose length equals the shortest distance,
    found by only ever stepping to a neighbor exactly one hop further away. Neighbor
    order is sorted, so paths come out in a deterministic order too.
    """
    target_distance = distances[target]
    results: list[tuple[Relationship, ...]] = []

    def dfs(node: str, path: tuple[Relationship, ...]) -> None:
        if node == target:
            results.append(path)
            return
        if len(path) >= target_distance:
            return
        for neighbor, rel in _neighbors(model, node):
            if distances.get(neighbor) == distances[node] + 1:
                dfs(neighbor, path + (rel,))

    dfs(anchor, ())
    return results


def find_join_path(model: SemanticModel, anchor: str, target: str) -> tuple[JoinStep, ...]:
    """Resolve the join path from `anchor` to `target` through declared relationships.

    Raises NoJoinPathError if the datasets aren't connected, AmbiguousJoinPathError
    if more than one shortest path connects them.
    """
    if anchor == target:
        return ()

    distances = _shortest_distances(model, anchor)
    if target not in distances:
        raise NoJoinPathError(
            f"no join path from {anchor!r} to {target!r} through the declared relationships"
        )

    paths = _enumerate_shortest_paths(model, anchor, target, distances)
    if len(paths) > 1:
        rendered = [" -> ".join(f"{r.from_dataset}.{r.from_column}={r.to_dataset}.{r.to_column}" for r in p) for p in paths]
        raise AmbiguousJoinPathError(
            f"ambiguous join path from {anchor!r} to {target!r}: {len(paths)} equally short paths found: "
            + "; ".join(rendered)
        )

    rel_path = paths[0]
    steps: list[JoinStep] = []
    current = anchor
    for rel in rel_path:
        if rel.from_dataset == current:
            steps.append(
                JoinStep(
                    dataset=rel.to_dataset,
                    from_dataset=current,
                    from_column=rel.from_column,
                    to_column=rel.to_column,
                )
            )
            current = rel.to_dataset
        else:
            steps.append(
                JoinStep(
                    dataset=rel.from_dataset,
                    from_dataset=current,
                    from_column=rel.to_column,
                    to_column=rel.from_column,
                )
            )
            current = rel.from_dataset
    return tuple(steps)
