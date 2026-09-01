-- R1: comparing sales results by week and by channel.
--
-- Small pre-aggregated result set (one row per week x channel), so a single
-- index on the column most likely to be filtered on is enough -- no need
-- for the same multi-index treatment fact_sales gets.

{{
    config(
        post_hook=[create_index('IX_sales_by_week_channel_channel_code', 'channel_code')]
    )
}}

select
    DATEPART(YEAR, order_ts)     as sale_year,
    DATEPART(ISO_WEEK, order_ts) as sale_iso_week,
    channel_code,
    SUM(gross_amount)            as gross_amount,
    SUM(quantity)                as quantity
from {{ ref('fact_sales') }}
group by
    DATEPART(YEAR, order_ts),
    DATEPART(ISO_WEEK, order_ts),
    channel_code
