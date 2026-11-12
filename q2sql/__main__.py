"""CLI: python -m q2sql --model model.json --contract contract_a.json --dialect duckdb"""

from __future__ import annotations

import argparse
import json
import sys

from .errors import ContractToSqlError
from .generate import generate_sql


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m q2sql", description="Turn a query contract into SQL.")
    parser.add_argument("--model", required=True, help="path to the semantic model JSON file")
    parser.add_argument("--contract", required=True, help="path to the contract JSON file")
    parser.add_argument("--dialect", required=True, help="duckdb | postgres")
    args = parser.parse_args(argv)

    with open(args.model) as f:
        model = json.load(f)
    with open(args.contract) as f:
        contract = json.load(f)

    try:
        sql = generate_sql(model, contract, args.dialect)
    except ContractToSqlError as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
