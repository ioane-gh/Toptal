-- R1: comparing sales results by week and by channel.

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
