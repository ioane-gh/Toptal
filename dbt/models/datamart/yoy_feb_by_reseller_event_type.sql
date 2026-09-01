-- R2: is Feb 2020 better than Feb 2019, filtered by reseller and event type.
--
-- Wide, not long: the report is a direct comparison, so 2019 and 2020 land
-- as columns on the same (event_type, reseller_id) row instead of as
-- separate rows -- a consumer shouldn't have to pivot or self-join this
-- table just to answer the question the report exists to answer.
--
-- Small pre-aggregated result set, one index on the grouping column
-- reports filter by most (reseller_id) is enough here.

{{
    config(
        post_hook=[create_index('IX_yoy_feb_by_reseller_event_type_reseller_id', 'reseller_id')]
    )
}}

with feb_sales as (
    select
        DATEPART(YEAR, order_ts) as sale_year,
        event_type,
        reseller_id,
        gross_amount,
        quantity
    from {{ ref('fact_sales') }}
    where DATEPART(MONTH, order_ts) = 2
      and DATEPART(YEAR, order_ts) in (2019, 2020)
)

select
    event_type,
    reseller_id,
    SUM(case when sale_year = 2019 then gross_amount else 0 end) as gross_amount_2019,
    SUM(case when sale_year = 2020 then gross_amount else 0 end) as gross_amount_2020,
    SUM(case when sale_year = 2020 then gross_amount else 0 end)
        - SUM(case when sale_year = 2019 then gross_amount else 0 end)            as gross_amount_yoy_change,
    (SUM(case when sale_year = 2020 then gross_amount else 0 end)
        - SUM(case when sale_year = 2019 then gross_amount else 0 end))
        / NULLIF(SUM(case when sale_year = 2019 then gross_amount else 0 end), 0) as gross_amount_yoy_change_pct,
    SUM(case when sale_year = 2019 then quantity else 0 end)                      as quantity_2019,
    SUM(case when sale_year = 2020 then quantity else 0 end)                      as quantity_2020
from feb_sales
group by
    event_type,
    reseller_id
