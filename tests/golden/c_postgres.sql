WITH "base" AS (
  SELECT
    "dim_store"."region" AS "dim_store__region",
    "fact_sales"."order_id" AS "order_id"
  FROM "fact_sales"
  LEFT JOIN "dim_store" ON "fact_sales"."store_id" = "dim_store"."store_id"
)
SELECT "region", "order_count"
FROM (
  SELECT
    "dim_store__region" AS "region",
    COUNT(DISTINCT "order_id") AS "order_count",
    0 AS "__sort_key"
  FROM "base"
  GROUP BY "dim_store__region"
  UNION ALL
  SELECT
    'Total' AS "region",
    COUNT(DISTINCT "order_id") AS "order_count",
    1 AS "__sort_key"
  FROM "base"
) AS "combined"
ORDER BY "__sort_key", "region"
