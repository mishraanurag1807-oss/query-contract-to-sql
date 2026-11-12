"""Golden-file tests: a diff here means generated SQL text changed behaviour."""

from pathlib import Path

import pytest

from q2sql import generate_sql

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.mark.parametrize("contract_name", ["a", "b", "c"])
@pytest.mark.parametrize("dialect", ["duckdb", "postgres"])
def test_golden_sql(model, request, contract_name, dialect):
    contract = request.getfixturevalue(f"contract_{contract_name}")
    sql = generate_sql(model, contract, dialect)
    expected = (GOLDEN_DIR / f"{contract_name}_{dialect}.sql").read_text()
    assert sql + "\n" == expected
