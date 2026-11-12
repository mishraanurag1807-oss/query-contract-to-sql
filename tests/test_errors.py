"""Honest failure: every bad input raises the specific named error, never
silently-wrong SQL."""

import pytest

from q2sql import (
    AmbiguousJoinPathError,
    InvalidContractError,
    InvalidModelError,
    NoJoinPathError,
    UnknownDimensionError,
    UnknownMetricError,
    UnsupportedContractError,
    generate_sql,
)


def test_unknown_metric(model):
    contract = {"metrics": [{"name": "nonexistent_metric"}], "group_by": [], "filters": []}
    with pytest.raises(UnknownMetricError):
        generate_sql(model, contract, "duckdb")


def test_unknown_dimension_in_group_by(model):
    contract = {"metrics": [{"name": "total_revenue"}], "group_by": ["nonexistent_dim"], "filters": []}
    with pytest.raises(UnknownDimensionError):
        generate_sql(model, contract, "duckdb")


def test_unknown_dimension_in_filter(model):
    contract = {
        "metrics": [{"name": "total_revenue"}],
        "group_by": [],
        "filters": [{"field": "nonexistent_dim", "op": "=", "value": 1, "model": "dim_store"}],
    }
    with pytest.raises(UnknownDimensionError):
        generate_sql(model, contract, "duckdb")


def _disconnected_dataset_model():
    return {
        "datasets": [
            {"name": "fact", "grain": "row"},
            {"name": "island", "grain": "row"},
        ],
        "relationships": [],
        "metrics": [{"name": "m", "agg": "sum", "expression": "val", "model": "fact", "measure_class": "additive"}],
        "dimensions": [{"name": "iso", "model": "island"}],
    }


def test_no_join_path(model):
    bad_model = _disconnected_dataset_model()
    contract = {"metrics": [{"name": "m"}], "group_by": ["iso"], "filters": []}
    with pytest.raises(NoJoinPathError):
        generate_sql(bad_model, contract, "duckdb")


def _ambiguous_join_model():
    return {
        "datasets": [
            {"name": "fact", "grain": "row"},
            {"name": "dim_target", "grain": "row"},
        ],
        "relationships": [
            {"from": "fact", "from_column": "a_id", "to": "dim_target", "to_column": "id", "cardinality": "many_to_one"},
            {"from": "fact", "from_column": "b_id", "to": "dim_target", "to_column": "id", "cardinality": "many_to_one"},
        ],
        "metrics": [{"name": "m", "agg": "sum", "expression": "val", "model": "fact", "measure_class": "additive"}],
        "dimensions": [{"name": "name", "model": "dim_target"}],
    }


def test_ambiguous_join_path(model):
    bad_model = _ambiguous_join_model()
    contract = {"metrics": [{"name": "m"}], "group_by": ["name"], "filters": []}
    with pytest.raises(AmbiguousJoinPathError):
        generate_sql(bad_model, contract, "duckdb")


def test_bad_compare_primary_not_in_periods(model):
    contract = {
        "metrics": [{"name": "total_revenue"}],
        "group_by": ["region"],
        "compare": {"dimension": "fiscal_year", "periods": ["2025", "2026"], "primary": "2027", "outputs": ["values"]},
    }
    with pytest.raises(InvalidContractError):
        generate_sql(model, contract, "duckdb")


def test_unsupported_totals_value(model):
    contract = {"metrics": [{"name": "total_revenue"}], "group_by": ["region"], "totals": "running"}
    with pytest.raises(UnsupportedContractError):
        generate_sql(model, contract, "duckdb")


def test_totals_without_group_by_is_unsupported(model):
    contract = {"metrics": [{"name": "total_revenue"}], "group_by": [], "totals": "grand"}
    with pytest.raises(UnsupportedContractError):
        generate_sql(model, contract, "duckdb")


def test_malformed_contract_missing_metrics(model):
    with pytest.raises(InvalidContractError):
        generate_sql(model, {"group_by": []}, "duckdb")


def test_malformed_contract_not_an_object(model):
    with pytest.raises(InvalidContractError):
        generate_sql(model, ["not", "an", "object"], "duckdb")


def test_malformed_model(contract_a):
    with pytest.raises(InvalidModelError):
        generate_sql({"datasets": []}, contract_a, "duckdb")


def test_unknown_dialect(model, contract_a):
    with pytest.raises(UnsupportedContractError):
        generate_sql(model, contract_a, "mysql")


def test_unsupported_operator(model):
    contract = {
        "metrics": [{"name": "total_revenue"}],
        "group_by": [],
        "filters": [{"field": "region", "op": "~", "value": "No.*", "model": "dim_store"}],
    }
    with pytest.raises(InvalidContractError):
        generate_sql(model, contract, "duckdb")


def test_delta_requires_exactly_two_periods(model):
    contract = {
        "metrics": [{"name": "total_revenue"}],
        "group_by": ["region"],
        "compare": {
            "dimension": "fiscal_year",
            "periods": ["2024", "2025", "2026"],
            "primary": "2026",
            "outputs": ["values", "delta"],
        },
    }
    with pytest.raises(UnsupportedContractError):
        generate_sql(model, contract, "duckdb")


def test_duplicate_metric_alias(model):
    contract = {
        "metrics": [{"name": "total_revenue"}, {"name": "total_revenue"}],
        "group_by": [],
        "filters": [],
    }
    with pytest.raises(InvalidContractError):
        generate_sql(model, contract, "duckdb")


def test_metrics_spanning_multiple_datasets_is_unsupported():
    multi_fact_model = {
        "datasets": [
            {"name": "fact_a", "grain": "row"},
            {"name": "fact_b", "grain": "row"},
        ],
        "relationships": [],
        "metrics": [
            {"name": "m_a", "agg": "sum", "expression": "val", "model": "fact_a", "measure_class": "additive"},
            {"name": "m_b", "agg": "sum", "expression": "val", "model": "fact_b", "measure_class": "additive"},
        ],
        "dimensions": [],
    }
    contract = {"metrics": [{"name": "m_a"}, {"name": "m_b"}], "group_by": [], "filters": []}
    with pytest.raises(UnsupportedContractError):
        generate_sql(multi_fact_model, contract, "duckdb")
