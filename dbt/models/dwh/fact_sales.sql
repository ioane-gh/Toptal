-- One row per ticket sold, unioning both sources. Required, not optional:
-- ~40% of resellers are THIRD_PARTY and have sales *only* in
-- raw_reseller.daily_sales, never in raw_b2b.orders (see NOTES.md). A fact
-- table built off the B2B side alone would silently exclude that entire
-- slice from every report that groups by reseller or region. No dedup
-- needed -- a given reseller's sales come from exactly one source, never
-- both, by construction.
--
-- Indexed on every column a datamart model filters/joins/groups by --
-- this is the table that actually gets large (millions of rows at the
-- `large` profile), everything downstream of it is a small pre-aggregated
-- result set where an index barely matters.

{{
    config(
        post_hook=[
            create_index('IX_fact_sales_reseller_id', 'reseller_id'),
            create_index('IX_fact_sales_order_ts', 'order_ts'),
            create_index('IX_fact_sales_region', 'region')
        ]
    )
}}

select
    source_row_id,
    seller_type,
    COALESCE(reseller_id, -1) as reseller_id,
    channel_code,
    order_ts,
    event_type,
    region,
    ticket_type_name,
    quantity,
    gross_amount,
    commission_rate,
    commission_amount,
    source_system
from {{ ref('stg_b2b_sales') }}

union all

select
    source_row_id,
    seller_type,
    reseller_id,
    channel_code,
    order_ts,
    event_type,
    region,
    ticket_type_name,
    quantity,
    gross_amount,
    commission_rate,
    commission_amount,
    source_system
from {{ ref('stg_reseller_sales') }}
