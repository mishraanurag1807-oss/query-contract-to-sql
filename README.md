# Query contract to SQL

Turns a typed JSON "contract" (metrics, group_by, filters, compare, totals) into a SQL
string against a declared semantic model. One function:

```python
generate_sql(semantic_model: dict, contract: dict, dialect: str) -> str
```

## 1. How to run

```
make venv   # creates .venv, installs duckdb + pytest (Python 3.11+ required)
make demo   # loads the brief's data into DuckDB, runs contracts A/B/C, prints SQL + results
make test   # runs the whole test suite (43 tests)
make bench  # prints measured generation times (p50/p95/p99/max)
```

CLI, for a single contract:

```
.venv/bin/python -m q2sql --model examples/model.json --contract examples/contract_a.json --dialect duckdb
```

## 2. Shape of the solution, and why

Four layers, each one small enough to hold in your head:

- **`model.py`** — loads the semantic model JSON once and indexes it (datasets,
  relationships, metrics, dimensions, keyed by name). Validates the model's own
  shape (`InvalidModelError`).
- **`contract.py`** — parses the contract JSON into typed dataclasses. Purely
  structural (shape, required keys, known operators) — it never touches the model.
- **`joins.py` + `plan.py`** — the planning layer. This is where the contract and
  the model actually meet: resolving metric/dimension names against the model,
  finding join paths, typing values, building the compare/totals logic. The output
  is a `LogicalPlan`: a dialect-independent dataclass tree.
- **`dialect.py` + `render.py`** — turns a `LogicalPlan` into a SQL string. `render.py`
  never asks "is this Postgres or DuckDB" — it only calls `Dialect` methods
  (`quote_ident`, `render_literal`, `safe_divide`, ...).

I put a full `LogicalPlan` in between contract and SQL text (rather than going
contract → string directly) because the planning questions — "what does this filter
resolve to", "is there a join path", "what does the totals row look like" — are
completely independent of *how* you spell a LEFT JOIN or a CAST. Mixing them means
every new dialect risks re-deciding semantics, and every new SQL feature risks
re-deciding syntax. Keeping them apart means `render.py` only needs to format
already-correct, already-validated pieces.

**Why no sqlglot, no ORM, no LLM.** The brief explicitly asks what a library would
do underneath. sqlglot would give me an AST I could print in multiple dialects, but
the hard part of this exercise was never SQL *syntax* — DuckDB and Postgres are
both so close to ANSI that an entire query only differs in one cast's type name
(see §3). The hard part was *semantics*: which joins are actually needed, how a
totals row gets computed without ever summing displayed rows, how a filter on a
different table gets derived instead of named, how comparable periods pivot into
columns. None of that is sqlglot's job — it's exactly the planning layer I'd still
have to write myself, so taking on a parser/dialect-matrix dependency to save
~60 lines of string-joining in `render.py` wasn't worth it. An LLM is a non-starter
on its own terms: it's neither deterministic nor fast enough, and the whole premise
of the exercise is that the LLM already did its part by producing the contract.

## 3. Decisions and assumptions

- **Join types.** Every dimension attached for grouping or output is a `LEFT JOIN`
  from the anchor fact table (`joins.py` + `render.py`'s `base` CTE), so a fact row
  whose dimension row is missing is still counted — it shows up under a `NULL`
  group. A *filter* on a dimension field is a `WHERE` condition against that same
  joined column, which genuinely drops non-matching rows, because `WHERE` runs after
  the join regardless of the join type. Both behaviors come from the same join; the
  difference is where the condition lives (`ON` vs `WHERE`), not which join I chose.
  Tested directly in `test_joins.py` by inserting a `fact_sales` row whose `store_id`
  doesn't exist in `dim_store`.
- **Only needed dimensions are joined.** The plan builder (`_PlanBuilder` in
  `plan.py`) walks the contract once — group_by, compare, filters, per-metric
  filters, in that order — and only calls `find_join_path` for datasets it actually
  needs a column from. `test_only_needed_dimensions_are_joined` checks `dim_store`
  never appears in contract A's SQL.
- **How periods/filter values are typed.** The semantic model declares no types at
  all. One rule, applied everywhere a contract supplies a value (`plan.py`,
  `coerce_literal_value`): a string made only of digits (optional leading `-`)
  becomes an integer literal; everything else is left alone. This is what lets
  `"periods": ["2025", "2026"]` compare correctly against an integer `fiscal_year`
  column, and it's the same function used for ordinary filter values — one rule,
  tested in `test_determinism.py`/`test_contracts.py` via the actual DuckDB results,
  not asserted in isolation.
- **pct_change as a fraction.** `delta / NULLIF(other, 0)`, cast to float before
  dividing. `1.5` means +150%; the `%` formatting is a presentation concern and
  deliberately isn't in the SQL.
- **How totals are computed (see §4 for the full walkthrough).** A totals row is
  never arithmetic on the displayed rows above it. It's a second aggregate query
  over the same `base` CTE, with no `GROUP BY`, `UNION ALL`'d with the grouped one.
  An explicit `__sort_key` column (0 for group rows, 1 for the totals row) guarantees
  the totals row sorts last; it's computed in the `combined` subquery and consumed —
  never selected — by the outermost query, which orders by it and then drops it.
- **Totals require a `group_by`.** A grand total over an already-ungrouped contract
  is either meaningless (nothing to total) or a silent duplicate of the one row that's
  already there. I raise `UnsupportedContractError` rather than guess — see §5.
- **Determinism.** No `set()`/`frozenset()` anywhere in the code path that produces
  SQL text (the one `set()` in `contract.py` is for alias-uniqueness *checking*, which
  only ever raises or doesn't — it never influences output order). Every list that
  becomes SQL text is built by iterating the contract's own JSON arrays (which
  `json.loads` parses in file order) or by explicitly sorting (`joins.py`'s neighbor
  expansion, so BFS visits datasets in the same order regardless of dict/hash state).
  Dict iteration is used freely elsewhere (e.g. `_PlanBuilder._projected`), because
  CPython dicts preserve insertion order — that guarantee is part of the language,
  not dependent on `PYTHONHASHSEED`, which only affects `str`/`frozenset` hashing and
  therefore `set` iteration order. Verified in `test_determinism.py`: 100 in-process
  repeats, plus four subprocess runs with different `PYTHONHASHSEED` values.
- **Where the dialect line is drawn.** DuckDB and Postgres are both aggressively
  Postgres-flavoured, so for *this* contract vocabulary the entire `Dialect` class is:
  identifier quoting (identical for both — double quotes, doubled to escape), literal
  rendering (identical), and the double-precision type name used in safe division
  (`DOUBLE` vs `DOUBLE PRECISION` — Postgres has no bare `DOUBLE`).
  `test_duckdb_and_postgres_differ_only_in_the_dialect_boundary` asserts the two
  dialects' output differs on exactly one line, and only by that type name — proof
  that `plan.py`/`joins.py`/`contract.py` never see the dialect. A third dialect
  (Snowflake, say) would add `quote_ident` differences (Snowflake upper-cases
  unquoted identifiers) and maybe a different `NULLIF`/cast spelling, but nothing
  above `Dialect` would change.
- **Filter operators.** `=, !=, <, <=, >, >=, in, not_in` — the closed set an
  analytics system needs. The contract carries a redundant `model` hint on each
  filter; I resolve the field against the semantic model's dimension declaration
  (source of truth) and only use the hint to cross-check for a contradiction
  (`InvalidContractError` if they disagree) — never to steer the join or bypass
  validation. Values are never interpolated into SQL as raw contract text; every
  literal goes through `Dialect.render_literal`.
- **Grand-total label type.** The totals row's group_by cell is the literal string
  `'Total'`. That's fine for a text dimension like `region`; grouping by a numeric
  dimension and asking for a grand total would hit a `UNION ALL` type mismatch in a
  strict dialect. Not exercised by any given contract — documented limitation, see §7.

## 4. Totals: the three tricky cases

All three go through the same mechanism (`render.py`'s `_render_aggregate_select`,
called once with `is_totals=False` and once with `is_totals=True`, then `UNION ALL`'d).
There is no `measure_class`-specific branch anywhere in the totals code — the
generality is structural, not a switch statement:

- **Additive (contract B's raw revenue sum).** Summing the displayed rows would
  *happen* to give the right value cells, because `SUM` is associative. I don't rely
  on that coincidence — the totals branch re-runs the identical `SUM(...)` expression
  against `base` with no `GROUP BY`, so it would still be correct even if it weren't
  additive.
- **Ratio-like derived columns (contract B's delta/pct_change).** This is where
  "sum the rows" visibly breaks: the right total pct_change is `260/450 = 57.8%`, not
  the sum (`195%`) or average (`65%`) of the three per-region percentages. The fix
  falls out of the architecture rather than needing a special case: delta and
  pct_change are computed in the *outermost* `SELECT`, from the `combined` subquery's
  own `total_revenue_2025`/`total_revenue_2026` columns — and `combined`'s totals row
  already holds the grand-total values (previous bullet). So the totals row's
  derived columns are automatically "the total row's own cells," because that's the
  only input the derived-column expression ever sees, for every row.
- **Distinct count (contract C).** Summing `COUNT(DISTINCT order_id)` per region
  gives 9, double-counting order `O-203` (it has lines in two regions). The totals
  branch instead runs `COUNT(DISTINCT order_id)` over the *whole* `base` with no
  `GROUP BY`, which counts `O-203` once — 8. Same code path as the additive case;
  it's correct because `COUNT(DISTINCT ...)` is being asked fresh, not added up.

## 5. Error handling

All in `q2sql/errors.py`, all subclasses of `ContractToSqlError`:

| Error | Raised when |
|---|---|
| `InvalidModelError` | The semantic model JSON is malformed: missing/wrong-typed keys, a relationship or metric pointing at an undeclared dataset, duplicate names. |
| `InvalidContractError` | The contract is malformed (missing `metrics`, bad types, unsupported operator) or internally inconsistent (duplicate output alias, a filter's `model` hint contradicting the semantic model, `compare.primary` not in `compare.periods`). |
| `UnknownMetricError` | A contract names a metric the model doesn't declare. |
| `UnknownDimensionError` | A contract names a dimension (in `group_by`, a filter, or `compare.dimension`) the model doesn't declare. |
| `NoJoinPathError` | A needed dimension's dataset isn't reachable from the anchor through any declared relationship. |
| `AmbiguousJoinPathError` | More than one shortest path connects the anchor to a needed dataset — the generator refuses to guess which one is "correct" for the business. |
| `UnsupportedContractError` | The contract is valid but asks for something not implemented: an unknown dialect, `totals` other than `"grand"`, `totals` without `group_by`, `delta`/`pct_change` with periods != 2, metrics spanning more than one fact table. |

Every message says what was wrong and, where there's a natural "where" (a contract
path, a model path, a dataset name), includes it. See `tests/test_errors.py` for one
test per row above.

## 6. Adding a feature without rewriting what exists

**A `having` clause.** `having` filters on an aggregated value (e.g. `total_revenue >
500`), which is a different scope from `filters` (row-level, pre-aggregation). New
pieces: a `HavingCondition` dataclass next to `BaseCondition` in `plan.py`, a
`having: tuple[HavingCondition, ...]` field on `LogicalPlan`, a small
`_parse_having`/`_build_having_condition` following the exact shape of the existing
filter builders, and in `render.py` one new line appended to the grouped/totals
`SELECT`s (and skipped when the measure's grain is ungrouped, same as `GROUP BY`
today). Nothing in `model.py`, `joins.py`, `contract.py`'s metric/group_by parsing,
or the `Dialect` class changes.

**A month-by-month trend.** This is `compare` generalized from 2 fixed periods to
"however many buckets `fiscal_quarter`/a date-trunc produces," each becoming either a
column (today's shape) or a row. The clean way in: a new `trend` contract key parsed
in `contract.py` next to `compare`, a `_build_trend_plan` in `plan.py` that's
structurally `_build_compare_plan` with `periods` replaced by "distinct values of the
trend dimension found at plan time" — except a trend is usually wanted as *rows*, not
pivoted columns, which is actually simpler than compare: it's just an ordinary
`GROUP BY trend_dimension, <existing group_by>`, so most of it is already free from
the group_by path. `render.py`'s `_render_aggregate_select` wouldn't need to change
at all; only `plan.py` grows a sibling builder next to `_build_compare_plan`.

**A second fact table.** This is the fan-out / chasm-trap problem, and it's why
`_PlanBuilder._resolve_anchor` currently raises `UnsupportedContractError` the moment
a contract's metrics span more than one dataset — I didn't want to ship a plausible-
looking join that's silently wrong under fan-out. The right fix isn't "join both fact
tables": joining `fact_sales` to a second fact table directly multiplies rows on
whatever grain they share (the classic chasm trap), inflating every `SUM`. The
correct shape is to aggregate each fact table *separately*, each down to the grain of
the dimensions actually requested (e.g. one `GROUP BY region` subquery per fact
table, each its own `base`/aggregate pair exactly like today, just parameterized by
which fact table it anchors), and only join those two already-aggregated results to
each other on the shared dimension key, with `FULL OUTER JOIN` so a region present in
only one fact table doesn't get dropped. Concretely: `plan.py` would gain a
`LogicalPlan` that's a list of per-fact sub-plans plus a final combine step, and
`render.py` would gain one new top-level shape (join of aggregates) alongside today's
single-fact shape. `joins.py`, `dialect.py`, and the per-fact planning code
(`_build_metric_output`, `_build_compare_plan`, the totals mechanism) are all reused
unchanged, once per fact table.

## 7. What breaks first at 10x contract vocabulary

- **Join path search and ambiguity handling.** BFS over relationships is fine at this
  scale; at 50+ datasets with multiple relationship "families" (e.g. a dimension
  reachable two reasonable ways — by store and by region-override), genuine
  ambiguity stops being a corner case and starts being common. Today's answer is "ask
  the contract author to be unambiguous" (refuse); at scale I'd want the model to be
  able to name a *preferred* relationship per dimension pair, so 95% of contracts
  don't need to specify a path at all and the remaining 5% can when the model itself
  is ambiguous.
- **Rule sprawl in one module.** Right now every contract feature (filters, per-metric
  filters, compare, totals) is a function in `plan.py`. At 10x feature count that file
  becomes the thing nobody wants to touch. I'd split it into one module per feature
  (`filters.py`, `compare.py`, `totals.py`) behind a tiny internal protocol
  (`resolve(contract_fragment, builder) -> partial plan contribution`), with
  `build_plan` reduced to orchestration. I didn't do this now because at 3 features
  the extra indirection would cost more readability than it buys.
- **Validation messages.** `UnknownMetricError`'s "declared metrics are [...]" gets
  unusable at 500 metric names. I'd switch to "did you mean X" (edit-distance against
  declared names) once the list is too long to read.
- **Model indexing and caching.** `load_semantic_model` re-parses the JSON on every
  `generate_sql` call; the benchmark shows this is still sub-millisecond even at 500
  metrics/dimensions (§8), so I deliberately didn't add caching — it would be
  complexity (cache invalidation, `id()`-based keys that can collide after garbage
  collection) bought against a problem the numbers say doesn't exist yet. If a future
  model made this a real cost, I'd cache keyed by object identity with a correctness
  check (`cached_dict is semantic_model`), not by anything content-derived (hashing a
  500-metric dict every call would cost more than it saves).
- **Plugin registry for features.** Once `having`, trends, and a second fact table
  all exist, `build_plan`'s current "if compare, else if totals, else plain" ladder in
  `render.py` would be worth replacing with an ordered list of feature handlers that
  each contribute to the plan/SQL independently — but only once there are enough
  features that the ladder is actually hard to read, not before.

## 8. Measured generation times

From `make bench` (2000 iterations per contract/dialect after 200 warmup iterations,
`time.perf_counter_ns`, contract-in to SQL-string-out only):

```
-- given semantic model (3 datasets, 2 metrics, 3 dimensions) --
A (aliased metrics) [duckdb]           p50=  0.0308ms  p95=  0.0363ms  p99=  0.0648ms  max=  0.0736ms
B (compare + totals) [duckdb]          p50=  0.0449ms  p95=  0.0526ms  p99=  0.0823ms  max=  0.8163ms
C (distinct count + totals) [duckdb]   p50=  0.0275ms  p95=  0.0306ms  p99=  0.0660ms  max=  0.0841ms
A (aliased metrics) [postgres]         p50=  0.0308ms  p95=  0.0360ms  p99=  0.0662ms  max=  0.0732ms
B (compare + totals) [postgres]        p50=  0.0467ms  p95=  0.0539ms  p99=  0.0870ms  max=  0.1058ms
C (distinct count + totals) [postgres] p50=  0.0280ms  p95=  0.0311ms  p99=  0.0696ms  max=  0.0898ms

-- synthetic large model (50 datasets, 500 metrics, 500 dimensions) --
synthetic (3 metrics, 3 group_by, totals) [duckdb] p50=  1.3545ms  p95=  1.4215ms  p99=  1.9214ms  max=  2.2614ms

All p99 under 10ms: True
```

Machine: an ordinary laptop (Apple Silicon Mac), CPython 3.12.16, no JIT tuning.
Even the synthetic model — 50 datasets, 500 metrics, 500 dimensions, a contract
touching 3 metrics / 3 group_by dims / a cross-dataset filter / a grand total — comes
in at p99 ≈ 1.9ms, roughly 5x headroom under the 10ms budget. These are real numbers
from this run, not rounded up or invented.

## 9. What I cut, and what I'd do next

**Cut:**
- Dimension-level `column` aliasing beyond the given model (the `Dimension.column`
  field exists and defaults to the dimension's `name`, so it's there if a future model
  needs it, but nothing in the given model exercises it).
- Non-"grand" totals (subtotals per group level, running totals) — `totals` only
  accepts `"grand"` today; anything else is an honest `UnsupportedContractError`
  rather than a guess.
- A second fact table, in code (the design is in §6, but `_resolve_anchor` refuses
  multi-dataset metric sets rather than attempting the fan-out-safe join).
- Caching the parsed semantic model across calls — see §7; the numbers say it isn't
  needed yet.

**What I'd do next**, in order: (1) a `having` clause and month-by-month trend, both
sketched concretely in §6 and both genuinely small additions given the current
shape; (2) the second-fact-table join, since it's the thing most likely to come up
in the follow-up conversation; (3) a Snowflake `Dialect` subclass, mostly to prove
the dialect boundary holds up against a less Postgres-like target than DuckDB.
