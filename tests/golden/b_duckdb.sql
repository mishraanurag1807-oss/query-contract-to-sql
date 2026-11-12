WITH "base" AS (
  SELECT
    "dim_store"."region" AS "region",
    "dim_calendar"."fiscal_year" AS "fiscal_year",
    "fact_sales"."revenue" AS "revenue"
  FROM "fact_sales"
  LEFT JOIN "dim_store" ON "fact_sales"."store_id" = "dim_store"."store_id"
  LEFT JOIN "dim_calendar" ON "fact_sales"."order_date" = "dim_calendar"."date"
  WHERE "dim_calendar"."fiscal_year" IN (2025, 2026)
)
SELECT "region", "total_revenue_2025", "total_revenue_2026", ("total_revenue_2026" - "total_revenue_2025") AS "total_revenue_delta", CAST(("total_revenue_2026" - "total_revenue_2025") AS DOUBLE) / NULLIF("total_revenue_2025", 0) AS "total_revenue_pct_change"
FROM (
  SELECT
    "region" AS "region",
    SUM(CASE WHEN "fiscal_year" = 2025 THEN "revenue" END) AS "total_revenue_2025",
    SUM(CASE WHEN "fiscal_year" = 2026 THEN "revenue" END) AS "total_revenue_2026",
    0 AS "__sort_key"
  FROM "base"
  GROUP BY "region"
  UNION ALL
  SELECT
    'Total' AS "region",
    SUM(CASE WHEN "fiscal_year" = 2025 THEN "revenue" END) AS "total_revenue_2025",
    SUM(CASE WHEN "fiscal_year" = 2026 THEN "revenue" END) AS "total_revenue_2026",
    1 AS "__sort_key"
  FROM "base"
) AS "combined"
ORDER BY "__sort_key", "region"
