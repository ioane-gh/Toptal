-- R1: comparing sales results by week and by channel.
--
-- Grain is distinct (sale_iso_week, channel_code) -- no sale_year column,
-- since ISO week 5 of 2019 and week 5 of 2020 are meant to compare as "the
-- same week" here, not stay separate rows (that per-year separation is
-- what yoy_feb_by_reseller_event_type is for, on the Feb slice
-- specifically). gross_amount_by_year / quantity_by_year keep the yearly
-- detail visible in a single row per week+channel via STRING_AGG, rather
-- than requiring a wide per-year column for every year the dataset might
-- ever span.

with weekly_year as (
    select
        DATEPART(ISO_WEEK, order_ts) as sale_iso_week,
        DATEPART(YEAR, order_ts)     as sale_year,
        channel_code,
        SUM(gross_amount)            as gross_amount,
        SUM(quantity)                as quantity
    from {{ ref('fact_sales') }}
    group by
        DATEPART(ISO_WEEK, order_ts),
        DATEPART(YEAR, order_ts),
        channel_code
)

select
    sale_iso_week,
    channel_code,
    SUM(gross_amount) as gross_amount,
    SUM(quantity)      as quantity,
    STRING_AGG(CONCAT(sale_year, ':', CAST(gross_amount as varchar(30))), ', ')
        WITHIN GROUP (order by sale_year)                                       as gross_amount_by_year,
    STRING_AGG(CONCAT(sale_year, ':', CAST(quantity as varchar(30))), ', ')
        WITHIN GROUP (order by sale_year)                                       as quantity_by_year
from weekly_year
group by
    sale_iso_week,
    channel_code
