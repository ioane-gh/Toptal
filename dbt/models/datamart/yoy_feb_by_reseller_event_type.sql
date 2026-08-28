-- R2: is Feb 2020 better than Feb 2019, filtered by reseller and event type.

select
    DATEPART(YEAR, order_ts) as sale_year,
    event_type,
    reseller_id,
    SUM(gross_amount)        as gross_amount,
    SUM(quantity)            as quantity
from {{ ref('fact_sales') }}
where DATEPART(MONTH, order_ts) = 2
  and DATEPART(YEAR, order_ts) in (2019, 2020)
group by
    DATEPART(YEAR, order_ts),
    event_type,
    reseller_id
