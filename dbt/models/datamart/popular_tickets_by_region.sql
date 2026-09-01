-- R4: most popular tickets per region.
--
-- rank_in_region lets a consumer answer "the" most popular ticket type per
-- region directly (WHERE rank_in_region = 1) rather than re-deriving the
-- ranking themselves from a flat quantity aggregation. RANK(), not
-- ROW_NUMBER(): a tie in quantity should show as a shared #1, not an
-- arbitrary tie-break -- that's what "popular" actually means here.
--
-- Small pre-aggregated result set, one index on region is enough here --
-- WHERE region = ... / WHERE rank_in_region = 1 are the two access
-- patterns, and rank_in_region is computed, not indexable pre-materialization.

{{
    config(
        post_hook=[create_index('IX_popular_tickets_by_region_region', 'region')]
    )
}}

with region_totals as (
    select
        region,
        ticket_type_name,
        SUM(quantity) as quantity
    from {{ ref('fact_sales') }}
    group by
        region,
        ticket_type_name
)

select
    region,
    ticket_type_name,
    quantity,
    RANK() over (partition by region order by quantity desc) as rank_in_region
from region_totals
