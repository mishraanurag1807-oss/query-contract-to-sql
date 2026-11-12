"""make demo: load the brief's data into DuckDB, run contracts A/B/C, print the
generated SQL and the result table for each."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures import fresh_connection  # noqa: E402

from q2sql import generate_sql  # noqa: E402

EXAMPLES = ROOT / "examples"


def print_table(cols: list[str], rows: list[tuple]) -> None:
    widths = [max(len(c), *(len(str(r[i])) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    header = " | ".join(c.ljust(w) for c, w in zip(cols, widths))
    print(header)
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(str(v).ljust(w) for v, w in zip(r, widths)))


def run(name: str, contract_file: str, dialect: str = "duckdb") -> None:
    model = json.loads((EXAMPLES / "model.json").read_text())
    contract = json.loads((EXAMPLES / contract_file).read_text())
    sql = generate_sql(model, contract, dialect)

    print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
    print(sql)
    print()

    con = fresh_connection()
    res = con.execute(sql)
    cols = [d[0] for d in res.description]
    rows = res.fetchall()
    print_table(cols, rows)
    con.close()


def main() -> None:
    run("Contract A — aliased metrics, per-metric filter, cross-table filter", "contract_a.json")
    run("Contract B — compare with derived columns + grand total", "contract_b.json")
    run("Contract C — distinct count with totals", "contract_c.json")


if __name__ == "__main__":
    main()
