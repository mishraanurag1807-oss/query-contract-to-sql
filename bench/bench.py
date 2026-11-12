"""Benchmark: measures contract-in -> SQL-string-out time with perf_counter_ns,
warms up, runs 1000+ iterations per (contract, dialect), and reports p50/p95/p99/max.

Also builds a synthetic larger semantic model (50 datasets, 500 metrics+dimensions)
to show how generation time scales, with a comparable contract against it.

Run: .venv/bin/python bench/bench.py   (or `make bench`)
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from q2sql import generate_sql  # noqa: E402

ITERATIONS = 2000
WARMUP = 200


def percentile(sorted_ns: list[int], p: float) -> float:
    idx = min(len(sorted_ns) - 1, int(len(sorted_ns) * p))
    return sorted_ns[idx] / 1e6


def measure(label: str, model: dict, contract: dict, dialect: str) -> dict:
    for _ in range(WARMUP):
        generate_sql(model, contract, dialect)

    samples = []
    for _ in range(ITERATIONS):
        start = time.perf_counter_ns()
        generate_sql(model, contract, dialect)
        samples.append(time.perf_counter_ns() - start)
    samples.sort()

    stats = {
        "label": label,
        "p50_ms": round(percentile(samples, 0.50), 4),
        "p95_ms": round(percentile(samples, 0.95), 4),
        "p99_ms": round(percentile(samples, 0.99), 4),
        "max_ms": round(samples[-1] / 1e6, 4),
    }
    print(
        f"{label:38s} p50={stats['p50_ms']:>8.4f}ms  p95={stats['p95_ms']:>8.4f}ms  "
        f"p99={stats['p99_ms']:>8.4f}ms  max={stats['max_ms']:>8.4f}ms"
    )
    return stats


def build_synthetic_model(n_datasets: int = 50, n_metrics: int = 500, n_dimensions: int = 500) -> dict:
    random.seed(1234)  # fixed seed: the synthetic model itself must also be reproducible
    datasets = [{"name": "fact_synth", "grain": "row"}]
    for i in range(n_datasets - 1):
        datasets.append({"name": f"dim_synth_{i}", "grain": "row"})

    relationships = []
    for i in range(n_datasets - 1):
        relationships.append(
            {
                "from": "fact_synth",
                "from_column": f"dim_synth_{i}_id",
                "to": f"dim_synth_{i}",
                "to_column": "id",
                "cardinality": "many_to_one",
            }
        )

    metrics = []
    for i in range(n_metrics):
        agg = "sum" if i % 5 != 0 else "count_distinct"
        metrics.append(
            {
                "name": f"metric_{i}",
                "agg": agg,
                "expression": f"value_{i % 20}",
                "model": "fact_synth",
                "measure_class": "additive" if agg == "sum" else "distinct_count",
            }
        )

    dimensions = []
    for i in range(n_dimensions):
        target_dataset = f"dim_synth_{i % (n_datasets - 1)}"
        dimensions.append({"name": f"dimension_{i}", "model": target_dataset})

    return {"datasets": datasets, "relationships": relationships, "metrics": metrics, "dimensions": dimensions}


def build_synthetic_contract() -> dict:
    return {
        "metrics": [
            {"name": "metric_0", "as": "m0_filtered", "filters": [{"field": "dimension_1", "op": "=", "value": "x", "model": "dim_synth_1"}]},
            {"name": "metric_1"},
            {"name": "metric_7"},  # count_distinct
        ],
        "group_by": ["dimension_0", "dimension_2", "dimension_10"],
        "filters": [{"field": "dimension_3", "op": "=", "value": "y", "model": "dim_synth_3"}],
        "totals": "grand",
    }


def main() -> None:
    examples = ROOT / "examples"
    model = json.loads((examples / "model.json").read_text())
    contracts = {
        "A (aliased metrics)": json.loads((examples / "contract_a.json").read_text()),
        "B (compare + totals)": json.loads((examples / "contract_b.json").read_text()),
        "C (distinct count + totals)": json.loads((examples / "contract_c.json").read_text()),
    }

    print(f"Benchmark: {ITERATIONS} iterations per (contract, dialect), {WARMUP} warmup iterations.\n")

    print("-- given semantic model (3 datasets, 2 metrics, 3 dimensions) --")
    results = []
    for dialect in ("duckdb", "postgres"):
        for name, contract in contracts.items():
            results.append(measure(f"{name} [{dialect}]", model, contract, dialect))

    print("\n-- synthetic large model (50 datasets, 500 metrics, 500 dimensions) --")
    big_model = build_synthetic_model()
    big_contract = build_synthetic_contract()
    results.append(measure("synthetic (3 metrics, 3 group_by, totals) [duckdb]", big_model, big_contract, "duckdb"))

    print("\nAll p99 under 10ms:", all(r["p99_ms"] < 10 for r in results))


if __name__ == "__main__":
    main()
