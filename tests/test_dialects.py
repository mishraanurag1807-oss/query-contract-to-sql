"""Dialect tests. Postgres output is golden-checked always, and executed against
a live Postgres only when one is reachable (never required).
"""

import os

import pytest

from q2sql import UnsupportedContractError, generate_sql
from q2sql.dialect import get_dialect


def test_unknown_dialect_raises_named_error(model, contract_a):
    with pytest.raises(UnsupportedContractError):
        generate_sql(model, contract_a, "snowflake")


def test_duckdb_and_postgres_differ_only_in_the_dialect_boundary(model, contract_b):
    duckdb_sql = generate_sql(model, contract_b, "duckdb")
    postgres_sql = generate_sql(model, contract_b, "postgres")

    duckdb_lines = duckdb_sql.split("\n")
    postgres_lines = postgres_sql.split("\n")
    assert len(duckdb_lines) == len(postgres_lines)

    diffs = [i for i, (d, p) in enumerate(zip(duckdb_lines, postgres_lines)) if d != p]
    # The only line that should differ is the pct_change cast, and the only
    # difference on it should be DOUBLE vs DOUBLE PRECISION.
    assert diffs, "expected at least one differing line (the safe-division cast)"
    for i in diffs:
        assert duckdb_lines[i].replace("DOUBLE PRECISION", "DOUBLE") == postgres_lines[i].replace(
            "DOUBLE PRECISION", "DOUBLE"
        )
        assert "DOUBLE" in duckdb_lines[i]


def _postgres_dsn():
    return os.environ.get("Q2SQL_TEST_POSTGRES_DSN")


@pytest.mark.skipif(not _postgres_dsn(), reason="no live Postgres configured (Q2SQL_TEST_POSTGRES_DSN unset)")
def test_contract_b_on_live_postgres(model, contract_b):
    import psycopg2  # imported lazily: not a hard dependency of the test suite

    sql = generate_sql(model, contract_b, "postgres")
    con = psycopg2.connect(_postgres_dsn())
    try:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TEMP TABLE dim_store (store_id TEXT PRIMARY KEY, store_name TEXT, region TEXT);
            CREATE TEMP TABLE dim_calendar (date DATE PRIMARY KEY, fiscal_year INTEGER, fiscal_quarter TEXT);
            CREATE TEMP TABLE fact_sales (
                order_id TEXT, order_date DATE, ship_date DATE, store_id TEXT,
                channel TEXT, revenue DOUBLE PRECISION
            );
            INSERT INTO dim_store VALUES
              ('S1','Downtown','North'), ('S2','Uptown','North'),
              ('S3','Riverside','South'), ('S4','Hilltop','West');
            INSERT INTO dim_calendar VALUES
                ('2025-03-10',2025,'Q1'), ('2025-04-02',2025,'Q2'), ('2025-06-15',2025,'Q2'),
                ('2026-02-01',2026,'Q1'), ('2026-02-11',2026,'Q1'),
                ('2026-03-05',2026,'Q1'), ('2026-03-06',2026,'Q1'), ('2026-06-20',2026,'Q2');
            INSERT INTO fact_sales VALUES
              ('O-100','2025-03-10','2025-03-12','S1','Online',100),
              ('O-101','2025-04-02','2025-04-04','S3','Retail',200),
              ('O-102','2025-06-15','2025-06-17','S4','Online',150),
              ('O-200','2026-02-01','2026-02-03','S1','Online',120),
              ('O-201','2026-02-11','2026-02-13','S2','Retail', 80),
              ('O-202','2026-03-05','2026-03-07','S3','Online',300),
              ('O-203','2026-03-06','2026-03-08','S1','Retail', 50),
              ('O-203','2026-03-06','2026-03-08','S3','Retail', 70),
              ('O-204','2026-06-20','2026-06-22','S4','Retail', 90);
            """
        )
        cur.execute(sql)
        rows = {r[0]: r for r in cur.fetchall()}
        assert rows["Total"][1:3] == (450, 710)
    finally:
        con.close()


def test_dialect_quote_ident_escapes_embedded_quotes():
    d = get_dialect("duckdb")
    assert d.quote_ident('weird"name') == '"weird""name"'
