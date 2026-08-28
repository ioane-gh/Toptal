-- Plain current-state lookup, not historized: the reports filter/group by
-- reseller_id (a stable key), never by a reseller's mutable attributes, so
-- there's no point-in-time-correctness gap to close here. Covers every
-- reseller regardless of integration_type -- platform and third-party
-- resellers are both defined once in raw_b2b.resellers; only their orders
-- differ by source (raw_b2b.orders vs. raw_reseller.daily_sales).

select
    reseller_id,
    reseller_name,
    country,
    region,
    city,
    integration_type,
    is_active
from {{ source('raw_b2b', 'resellers') }}
