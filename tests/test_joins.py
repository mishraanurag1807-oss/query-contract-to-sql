"""Join semantics: attaching a dimension must not drop facts; filtering on a
dimension must genuinely restrict rows."""

from fixtures import fresh_connection

from q2sql import generate_sql


def _with_orphan_store_row(con):
    # S9 is not in dim_store at all.
    con.execute(
        "INSERT INTO fact_sales VALUES "
        "('O-900','2026-02-01','2026-02-03','S9','Online',999)"
    )
    return con


def test_left_join_keeps_facts_with_missing_dimension_row(model):
    con = _with_orphan_store_row(fresh_connection())
    contract = {"metrics": [{"name": "total_revenue"}], "group_by": ["region"], "filters": []}
    sql = generate_sql(model, contract, "duckdb")
    assert "LEFT JOIN" in sql

    rows = {r[0]: r[1] for r in con.execute(sql).fetchall()}
    assert None in rows  # the orphan store's region comes back NULL, not dropped
    assert rows[None] == 999.0

    grand_total = con.execute("SELECT SUM(revenue) FROM fact_sales").fetchone()[0]
    assert sum(v for v in rows.values()) == grand_total


def test_filter_on_dimension_genuinely_restricts_rows(model):
    con = _with_orphan_store_row(fresh_connection())
    contract = {
        "metrics": [{"name": "total_revenue"}],
        "group_by": [],
        "filters": [{"field": "region", "op": "=", "value": "North", "model": "dim_store"}],
    }
    sql = generate_sql(model, contract, "duckdb")
    (total,) = con.execute(sql).fetchone()
    # North = S1 + S2 only: 100+120+50 (S1) + 80 (S2) = 350. The orphan S9 row
    # (region NULL) must NOT be counted here, unlike the group-by case above.
    assert total == 350.0


def test_only_needed_dimensions_are_joined(model, contract_a):
    sql = generate_sql(model, contract_a, "duckdb")
    # contract_a filters on fiscal_year (dim_calendar) and channel (fact_sales only);
    # it never touches region, so dim_store must not appear at all.
    assert "dim_store" not in sql
    assert "dim_calendar" in sql
