"""Execute the generated SQL for contracts A, B, C against DuckDB and check the
exact expected results from the take-home brief."""

from q2sql import generate_sql


def _rows(con, sql):
    res = con.execute(sql)
    cols = [d[0] for d in res.description]
    return cols, res.fetchall()


def test_contract_a_aliased_metrics(model, contract_a, duckdb_con):
    sql = generate_sql(model, contract_a, "duckdb")
    cols, rows = _rows(duckdb_con, sql)
    assert cols == ["online_rev", "total_revenue"]
    assert rows == [(420.0, 710.0)]


def test_contract_b_compare_with_totals(model, contract_b, duckdb_con):
    sql = generate_sql(model, contract_b, "duckdb")
    cols, rows = _rows(duckdb_con, sql)
    assert cols == [
        "region",
        "total_revenue_2025",
        "total_revenue_2026",
        "total_revenue_delta",
        "total_revenue_pct_change",
    ]
    by_region = {r[0]: r for r in rows}
    assert rows[-1][0] == "Total"  # totals row always last

    north = by_region["North"]
    assert north[1:3] == (100.0, 250.0)
    assert north[3] == 150.0
    assert round(north[4], 3) == 1.5

    south = by_region["South"]
    assert south[1:3] == (200.0, 370.0)
    assert south[3] == 170.0
    assert round(south[4], 3) == 0.85

    west = by_region["West"]
    assert west[1:3] == (150.0, 90.0)
    assert west[3] == -60.0
    assert round(west[4], 3) == -0.4

    total = by_region["Total"]
    assert total[1:3] == (450.0, 710.0)
    assert total[3] == 260.0
    assert round(total[4], 4) == round(260 / 450, 4)  # 0.5778 ~ 57.8%, from the total row's own cells


def test_contract_c_distinct_count_with_totals(model, contract_c, duckdb_con):
    sql = generate_sql(model, contract_c, "duckdb")
    cols, rows = _rows(duckdb_con, sql)
    assert cols == ["region", "order_count"]
    by_region = {r[0]: r[1] for r in rows}
    assert by_region == {"North": 4, "South": 3, "West": 2, "Total": 8}  # not 9 — O-203 spans two regions
    assert rows[-1][0] == "Total"


def test_contract_b_totals_pct_change_is_not_sum_or_average_of_rows(model, contract_b, duckdb_con):
    """The totals row's pct_change must come from the total row's own two value
    cells (260/450), never the sum (150%+85%-40%=195%) or average (65%) of the
    per-region pct_change values.
    """
    sql = generate_sql(model, contract_b, "duckdb")
    _, rows = _rows(duckdb_con, sql)
    by_region = {r[0]: r for r in rows}
    total_pct = by_region["Total"][4]
    wrong_sum = sum(by_region[r][4] for r in ("North", "South", "West"))
    wrong_avg = wrong_sum / 3
    assert abs(total_pct - wrong_sum) > 0.01
    assert abs(total_pct - wrong_avg) > 0.01
    assert round(total_pct, 4) == round(260 / 450, 4)
