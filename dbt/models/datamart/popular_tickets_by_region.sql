-- R4: most popular tickets per region.

select
    region,
    ticket_type_name,
    SUM(quantity) as quantity
from {{ ref('fact_sales') }}
group by
    region,
    ticket_type_name
