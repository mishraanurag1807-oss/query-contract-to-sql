# My understanding (scratch, not a deliverable)

## Setting
- Walt take-home. An LLM fills in a typed JSON "contract" (metrics, group_by, filters,
  compare, totals). Something else — what I'm building — turns that contract into SQL
  that runs on the customer's warehouse. The generator never reads the warehouse, never
  guesses: everything it's allowed to know is in the semantic model JSON.
- Two hard constraints:
  1. Under ~10ms per contract, contract-in to SQL-string-out (execution not counted).
  2. Byte-for-byte deterministic: same contract -> same SQL, every run, every process,
     any PYTHONHASHSEED.

## Schema + data
- `dim_store(store_id PK, store_name, region)` — 4 rows, regions North/North/South/West.
- `dim_calendar(date PK, fiscal_year, fiscal_quarter)` — fiscal_year = calendar year.
  Must populate a row for every date used in fact_sales.
- `fact_sales(order_id, order_date, ship_date, store_id, channel, revenue)` — grain: one
  order line. Note O-203 has two lines in two different stores/regions (S1/North,
  S3/South) — this is the distinct-count double-counting trap.

## Semantic model
- `datasets`: fact_sales, dim_store, dim_calendar (name + grain only, no types).
- `relationships`: fact_sales.store_id -> dim_store.store_id (many_to_one),
  fact_sales.order_date -> dim_calendar.date (many_to_one). Directed, from fact to dim.
- `metrics`: total_revenue (sum of revenue on fact_sales, measure_class additive),
  order_count (count_distinct of order_id on fact_sales, measure_class distinct_count).
- `dimensions`: region (on dim_store), channel (on fact_sales), fiscal_year (on
  dim_calendar). No separate "column" field — name IS the column name on that model.
  I'll support an optional `column` override key for future-proofing but it's unused here.

## Contract A — aliased metrics, per-metric filter, cross-table filter
- Two readings of total_revenue in one query: one filtered to channel='Online' aliased
  `online_rev`, one unfiltered as `total_revenue`. Must use conditional aggregation in a
  single scan, not two queries.
- Top-level filter on fiscal_year=2026, but fiscal_year lives on dim_calendar, not
  fact_sales. The generator must derive the join itself — nothing in the contract names
  it. No group_by -> single row.
- Expected: online_rev=420, total_revenue=710.
  - Check against data: fiscal_year=2026 rows: O-200(120,Online,S1), O-201(80,Retail,S2),
    O-202(300,Online,S3), O-203 x2 (50 Retail S1, 70 Retail S3), O-204(90,Retail,S4).
    Online sum = 120+300 = 420. Total = 120+80+300+50+70+90 = 710. Matches.

## Contract B — compare with derived columns + grand total
- group_by region, compare fiscal_year over periods ["2025","2026"] (primary "2026"),
  outputs values/delta/pct_change, totals="grand".
- Periods become columns: total_revenue_2025, total_revenue_2026, total_revenue_delta,
  total_revenue_pct_change. One row per region + a Total row.
- Base data restricted to the two compared periods only.
- delta = primary - other. pct_change = delta / NULLIF(other, 0), as a fraction (1.5 =
  150%), presentation (the % sign) stays out of the SQL.
- Totals row: value cells are recomputed from base data at the grand grain (not summed
  from the group rows — though for additive measures the arithmetic happens to agree),
  and delta/pct_change are computed from the totals row's OWN cells, never
  summed/averaged from the rows above. 260/450 = 57.78% ~ 57.8%, not avg or sum of the
  per-region percentages (150+85-40)/3 = 65% or sum 195% — both wrong, confirms the rule.
- Periods arrive as strings ("2025") but fiscal_year is (implicitly, from the DDL)
  integer, and the model declares no types at all. Decision: a contract value that is a
  string of only digits (optional leading '-') is treated as an integer literal; this
  rule is applied uniformly to compare periods AND regular filter values, in one place,
  so there's one documented rule instead of two ad hoc ones.
- Expected: North 100/250/150/150.0%, South 200/370/170/85.0%, West 150/90/-60/-40.0%,
  Total 450/710/260/57.8%.
  - Verify from data: 2025 rows: O-100(100,Online,S1/North), O-101(200,Retail,S3/South),
    O-102(150,Online,S4/West). North=100, South=200, West=150. Total=450. Matches.
    2026 rows (excluding store joins missing — none here): North = O-200(120) +
    O-203/S1(50) = 170? Wait expected says North 2026 = 250. Let me recheck regions:
    S1=North, S2=North, S3=South, S4=West (from dim_store: S1 Downtown North, S2 Uptown
    North, S3 Riverside South, S4 Hilltop West). So 2026: O-200 S1/North 120, O-201
    S2/North 80, O-202 S3/South 300, O-203 S1/North 50 + O-203 S3/South 70, O-204
    S4/West 90. North = 120+80+50 = 250. South = 300+70 = 370. West = 90. Total = 710.
    Matches expected exactly (North 250, South 370, West 90).
  - delta North = 250-100=150, pct=150/100=150%. South: 370-200=170, 170/200=85%. West:
    90-150=-60, -60/150=-40%. Total: 710-450=260, 260/450=57.78%->57.8%. All match.

## Contract C — distinct count with totals
- order_count (count_distinct order_id) by region, totals="grand".
- North: S1+S2 orders. S1 orders: O-100, O-200, O-203. S2 orders: O-201. North orders =
  {O-100,O-200,O-203,O-201} = 4. South: S3 orders: O-101, O-202, O-203 = {O-101,O-202,
  O-203} = 3. West: S4 orders: O-102, O-204 = 2. Total distinct orders overall:
  {O-100,O-101,O-102,O-200,O-201,O-202,O-203,O-204} = 8 (O-203 counted once). Matches
  "North 4, South 3, West 2, Total 8 (not 9)".
- Totals must be a separate COUNT(DISTINCT ...) over the whole base, combined via UNION
  ALL with the grouped rows, never SUM(group values). This has to be general code, not a
  special case for count_distinct — the same totals mechanism must work for additive
  SUMs (where summing rows would happen to look right) and for compare's derived columns
  (where summing is visibly wrong), so the implementation is one mechanism, not three.
- Output order: explicit, deterministic, totals row always last. I'll use an internal
  sort-key column dropped from the final projection via an outer SELECT.

## Requirements checklist
- [ ] generate_sql(semantic_model, contract, dialect) -> str, <10ms, deterministic.
- [ ] DuckDB + one more dialect (picking Postgres per the other model's brief; PDF allows
      Postgres or Snowflake, my choice).
- [ ] Join semantics: LEFT JOIN for attaching dimensions (facts without a matching dim
      row still count, e.g. a sale at a store missing from dim_store); filters on a
      dimension field restrict rows for real (so filter conditions go in the WHERE
      clause against the joined column, not into the join's ON clause).
- [ ] Honest failure: named errors, never silently wrong SQL.
- [ ] Tests trustworthy enough for a refactor.
- [ ] Deliverables: code (one-command runnable against DuckDB), tests (one command),
      README with the specific reasoning sections asked for.

## Ambiguities / underspecified points and the choice I'm making
1. **Dimension "column" vs "name".** Model gives no separate column field. Choice:
   dimension's SQL column = its `name`, on the dataset given by `model`. I add an
   optional `column` override for extensibility, unused by the given model.
2. **Filter `model` field vs dimension's declared model.** Contract filters redundantly
   carry a `model` key. Choice: still resolve the field against the declared dimension
   in the semantic model (source of truth) and cross-check the contract's `model` key
   matches it — mismatch is an InvalidContractError, not a silent override. This is
   defense in depth per "never interpolate raw contract text into SQL unchecked".
3. **String/int typing for filter and compare values.** No types in model. Choice: a
   value that's a string of only digits (regex `^-?\d+$`) is treated as an integer
   literal everywhere (filters and compare periods), applied through one shared
   function. Documented and tested.
4. **Totals row for a grand total with group_by empty.** Not in any given contract.
   Choice: raise UnsupportedContractError("totals requires at least one group_by
   dimension") rather than silently duplicating the single row — degenerate case with no
   real meaning here.
5. **Multiple fact tables / metrics from different `model`s in one contract.** Not
   exercised by A/B/C (all metrics are on fact_sales). Choice: if a contract's metrics
   span more than one dataset, raise UnsupportedContractError for now (this is the
   "fan-out / chasm trap" the README has to talk through as a design question, not code
   I'm shipping for this round).
6. **Grand-total label type.** The totals row's group_by cell is the literal 'Total'
   (string). Fine for a text dimension like `region`; if group_by were on a numeric
   dimension, UNION ALL would hit a type mismatch. Documented limitation — out of scope
   since no given contract groups by a numeric dimension.
7. **Supported filter operators.** Given contracts only use `=`. I'll support
   `=, !=, <, <=, >, >=, in, not_in` since they're the obvious closed set an analytics
   system needs and cost little; anything else raises UnsupportedContractError.
8. **Second dialect choice.** Postgres (over Snowflake) — more common, free to test
   logic against mentally, and DuckDB's SQL surface is deliberately Postgres-like so the
   real dialect differences are small and worth naming precisely in the README.

## Plan (architecture) — see README for the final writeup; building now.
