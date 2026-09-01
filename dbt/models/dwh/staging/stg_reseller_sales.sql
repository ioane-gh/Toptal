-- Reseller/CSV side of fact_sales. raw_reseller.daily_sales only ever holds
-- rows the Python pipeline already validated (numeric quantity/price,
-- parseable dates, TOTAL_AMOUNT within tolerance of QUANTITY*UNIT_PRICE --
-- see src/ingestion/ingest_reseller.py) and stored as NVARCHAR(255), so this
-- model is a plain type cast, not re-validation.
--
-- commission_rate/commission_amount don't exist in the CSV at all -- see
-- NOTES.md's join path -- so they're resolved here by matching
-- (reseller_id, sale_date) against partnership_agreements' validity window,
-- the same rate history the B2B side already has baked into order_items.
--
-- The generator chains consecutive agreement periods so one period's
-- valid_to equals the next period's valid_from -- the same date value ends
-- one period and starts the next. An inclusive-inclusive join
-- (valid_from <= date <= valid_to) matches BOTH periods on that boundary
-- date and fans a row out into two. Fixed with a half-open interval
-- (valid_to exclusive -- the new period owns the boundary date) plus
-- OUTER APPLY ... TOP 1 so a match is structurally guaranteed to be at
-- most one row, not just correct for the cases already reasoned about.

select
    ds.id                                              as source_row_id,
    'RESELLER'                                         as seller_type,
    CAST(ds.reseller_id as int)                        as reseller_id,
    ds.sale_channel                                    as channel_code,
    CAST(ds.sale_date as datetime2(3))                 as order_ts,
    ds.event_type,
    ds.venue_region                                    as region,
    ds.ticket_type                                     as ticket_type_name,
    CAST(ds.quantity as int)                           as quantity,
    CAST(ds.total_amount as decimal(18,4))             as gross_amount,
    COALESCE(pa.commission_rate, 0)                    as commission_rate,
    CAST(ds.total_amount as decimal(18,4)) * COALESCE(pa.commission_rate, 0) as commission_amount,
    'RESELLER'                                         as source_system
from {{ source('raw_reseller', 'daily_sales') }} ds
outer apply (
    select top 1 a.commission_rate
    from {{ source('raw_b2b', 'partnership_agreements') }} a
    where a.reseller_id = CAST(ds.reseller_id as int)
      and a.valid_from <= CAST(ds.sale_date as date)
      and (a.valid_to is null or CAST(ds.sale_date as date) < a.valid_to)
    order by a.valid_from desc
) pa
