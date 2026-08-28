-- B2B-platform side of fact_sales: order_items already typed correctly at
-- the raw layer, so this is a straight join to resolve the display/grouping
-- attributes each report needs (channel_code, event_type, venue region,
-- ticket_type_name) -- none of which need their own dimension model, since
-- none of them change after creation in this dataset (see NOTES.md
-- "Removing organizers and customers" / "Choosing which tables to
-- snapshot").

select
    oi.order_item_id                       as source_row_id,
    o.seller_type,
    o.reseller_id,
    sc.channel_code,
    o.order_ts,
    e.event_type,
    v.region,
    tt.ticket_type_name,
    oi.quantity,
    oi.gross_amount,
    oi.commission_rate,
    oi.commission_amount,
    'B2B'                                  as source_system
from {{ source('raw_b2b', 'order_items') }} oi
join {{ source('raw_b2b', 'orders') }} o on o.order_id = oi.order_id
join {{ source('raw_b2b', 'events') }} e on e.event_id = oi.event_id
join {{ source('raw_b2b', 'venues') }} v on v.venue_id = e.venue_id
join {{ source('raw_b2b', 'ticket_types') }} tt on tt.ticket_type_id = oi.ticket_type_id
join {{ source('raw_b2b', 'sales_channels') }} sc on sc.channel_id = o.channel_id
