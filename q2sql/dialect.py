"""Dialect layer: the only place SQL text differs between warehouses.

The planner and renderer never special-case a dialect by name; they call these
methods and get back correct syntax. For DuckDB and Postgres the surface is
almost identical (both are Postgres-flavoured), so this class ends up small —
that's the point, not an accident. The one real difference encoded below is the
double-precision type name (DOUBLE vs DOUBLE PRECISION), used for safe division.
A third dialect (say Snowflake) would also need to override quote_ident
(backtick-free but case-folds unquoted identifiers differently) and possibly
render_literal for booleans/dates; nothing above the Dialect boundary would change.
"""

from __future__ import annotations

from .errors import UnsupportedContractError

_OP_SQL = {
    "=": "=",
    "!=": "<>",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
    "in": "IN",
    "not_in": "NOT IN",
}


class Dialect:
    name = "base"
    double_type = "DOUBLE"

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def render_literal(self, value) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return repr(value)
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
        raise UnsupportedContractError(f"cannot render literal of type {type(value).__name__}: {value!r}")

    def render_value_or_list(self, op: str, value) -> str:
        if op in ("in", "not_in"):
            items = ", ".join(self.render_literal(v) for v in value)
            return f"({items})"
        return self.render_literal(value)

    def op_sql(self, op: str) -> str:
        sql = _OP_SQL.get(op)
        if sql is None:
            raise UnsupportedContractError(f"operator {op!r} is not supported by the SQL renderer")
        return sql

    def cast_to_double(self, expr: str) -> str:
        return f"CAST({expr} AS {self.double_type})"

    def safe_divide(self, numerator_expr: str, denominator_expr: str) -> str:
        return f"{self.cast_to_double(numerator_expr)} / NULLIF({denominator_expr}, 0)"


class DuckDBDialect(Dialect):
    name = "duckdb"
    double_type = "DOUBLE"


class PostgresDialect(Dialect):
    name = "postgres"
    double_type = "DOUBLE PRECISION"


_DIALECTS: dict[str, type[Dialect]] = {
    "duckdb": DuckDBDialect,
    "postgres": PostgresDialect,
}


def get_dialect(name: str) -> Dialect:
    dialect_cls = _DIALECTS.get(name)
    if dialect_cls is None:
        raise UnsupportedContractError(f"unknown dialect {name!r}; supported dialects are {sorted(_DIALECTS)}")
    return dialect_cls()
