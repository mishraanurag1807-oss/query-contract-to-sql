WITH "base" AS (
  SELECT
    "fact_sales"."revenue" AS "revenue",
    "fact_sales"."channel" AS "channel"
  FROM "fact_sales"
  LEFT JOIN "dim_calendar" ON "fact_sales"."order_date" = "dim_calendar"."date"
  WHERE "dim_calendar"."fiscal_year" = 2026
)
SELECT "online_rev", "total_revenue"
FROM (
  SELECT
    SUM(CASE WHEN "channel" = 'Online' THEN "revenue" END) AS "online_rev",
    SUM("revenue") AS "total_revenue"
  FROM "base"
) AS "combined"
