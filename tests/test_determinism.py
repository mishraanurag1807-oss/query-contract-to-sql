"""Determinism tests: same contract must produce byte-for-byte identical SQL,
in-process and across processes with different PYTHONHASHSEED."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from q2sql import generate_sql

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("contract_name", ["a", "b", "c"])
@pytest.mark.parametrize("dialect", ["duckdb", "postgres"])
def test_repeated_generation_in_one_process_is_identical(model, request, contract_name, dialect):
    contract = request.getfixturevalue(f"contract_{contract_name}")
    outputs = {generate_sql(model, contract, dialect) for _ in range(100)}
    assert len(outputs) == 1


_SUBPROCESS_SCRIPT = """
import json, sys
sys.path.insert(0, {root!r})
from q2sql import generate_sql
model = json.load(open({model_path!r}))
contract = json.load(open({contract_path!r}))
print(generate_sql(model, contract, {dialect!r}))
"""


@pytest.mark.parametrize("contract_name", ["a", "b", "c"])
def test_generation_is_stable_across_hash_seeds(contract_name):
    model_path = ROOT / "examples" / "model.json"
    contract_path = ROOT / "examples" / f"contract_{contract_name}.json"
    script = _SUBPROCESS_SCRIPT.format(
        root=str(ROOT), model_path=str(model_path), contract_path=str(contract_path), dialect="duckdb"
    )

    outputs = set()
    for seed in ("0", "1", "42", "random"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
        )
        outputs.add(result.stdout)
    assert len(outputs) == 1
