"""The single public entry point."""

from __future__ import annotations

from .contract import parse_contract
from .dialect import get_dialect
from .model import load_semantic_model
from .plan import build_plan
from .render import render


def generate_sql(semantic_model: dict, contract: dict, dialect: str) -> str:
    """Turn a contract into a SQL string for the given dialect.

    Raises (see q2sql.errors): InvalidModelError, InvalidContractError,
    UnknownMetricError, UnknownDimensionError, NoJoinPathError,
    AmbiguousJoinPathError, UnsupportedContractError.
    """
    model = load_semantic_model(semantic_model)
    parsed_contract = parse_contract(contract)
    dialect_obj = get_dialect(dialect)
    plan = build_plan(model, parsed_contract)
    return render(plan, dialect_obj)
