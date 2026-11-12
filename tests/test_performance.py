"""A lenient perf assertion. See bench/bench.py for the real measurement with
proper warmup, 1000+ iterations, and p50/p95/p99/max reporting."""

import time

from q2sql import generate_sql

_ITERATIONS = 300


def test_generation_is_fast(model, contract_a, contract_b, contract_c):
    contracts = [contract_a, contract_b, contract_c]
    for _ in range(50):  # warmup
        for c in contracts:
            generate_sql(model, c, "duckdb")

    samples_ns = []
    for _ in range(_ITERATIONS):
        for c in contracts:
            start = time.perf_counter_ns()
            generate_sql(model, c, "duckdb")
            samples_ns.append(time.perf_counter_ns() - start)

    samples_ns.sort()
    p99_ns = samples_ns[int(len(samples_ns) * 0.99)]
    assert p99_ns < 10_000_000, f"p99 generation time {p99_ns / 1e6:.3f}ms exceeds 10ms"
