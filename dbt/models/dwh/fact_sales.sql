-- One row per ticket sold, unioning both sources. Required, not optional:
-- ~40% of resellers are THIRD_PARTY and have sales *only* in
-- raw_reseller.daily_sales, never in raw_b2b.orders (see NOTES.md). A fact
-- table built off the B2B side alone would silently exclude that entire
-- slice from every report that groups by reseller or region. No dedup
-- needed -- a given reseller's sales come from exactly one source, never
-- both, by construction.
--
-- Incremental, MERGE strategy keyed on (source_system, source_row_id) --
-- source_row_id alone isn't globally unique, since it's a per-source
-- surrogate (order_item_id on one side, daily_sales' identity column on the
-- other) and both start counting from 1. source_updated_at is the
-- watermark: order_items.updated_at on the B2B side, so mutate_b2b.py's
-- order corrections (quantity/gross_amount changes on an existing row, not
-- just new rows) land as MERGE updates instead of being missed entirely; on
-- the reseller side there's no row-level updated_at at all -- CSV files are
-- immutable, so raw_reseller.daily_sales rows are never mutated after
-- load -- so _ingested_at (when the row was loaded) stands in, matching the
-- file-identity-based incrementality the reseller ingestion side already
-- uses.
--
-- Indexed on every column a datamart model filters/joins/groups by --
-- this is the table that actually gets large (millions of rows at the
-- `large` profile), everything downstream of it is a small pre-aggregated
-- result set where an index barely matters. Post-hooks stay IF NOT
-- EXISTS-guarded so they're a no-op on every incremental run after the
-- first -- the indexes persist across MERGEs like any other SQL Server
-- index, they don't need rebuilding each time.

{{
    config(
        materialized='incremental',
        unique_key=['source_system', 'source_row_id'],
        incremental_strategy='merge',
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
    source_system,
    source_updated_at
from {{ ref('stg_b2b_sales') }}
{% if is_incremental() %}
where source_updated_at > (
    select COALESCE(MAX(source_updated_at), CAST('1900-01-01' as datetime2(3)))
    from {{ this }}
    where source_system = 'B2B'
)
{% endif %}

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
    source_system,
    source_updated_at
from {{ ref('stg_reseller_sales') }}
{% if is_incremental() %}
where source_updated_at > (
    select COALESCE(MAX(source_updated_at), CAST('1900-01-01' as datetime2(3)))
    from {{ this }}
    where source_system = 'RESELLER'
)
{% endif %}
