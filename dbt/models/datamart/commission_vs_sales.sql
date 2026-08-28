-- R3: commission rate vs. sales results, per reseller.

select
    f.reseller_id,
    r.reseller_name,
    f.commission_rate,
    SUM(f.commission_amount) as total_commission,
    SUM(f.gross_amount)      as gross_amount
from {{ ref('fact_sales') }} f
left join {{ ref('dim_resellers') }} r on r.reseller_id = f.reseller_id
where f.reseller_id is not null
group by
    f.reseller_id,
    r.reseller_name,
    f.commission_rate
