-- Smoke test: answers R1-R4 directly against the SQLite source
-- (data/source/b2b.db), before anything has been ingested. Run with:
--   sqlite3 data/source/b2b.db < tests/source_report_smoke.sql
-- Only covers B2B-platform sales (direct + platform-reseller); the
-- third-party-reseller CSV side of R1-R4 is smoke-tested separately once
-- raw_reseller is populated (see tests/verification.sql, Phase 8).

-- R1: Sales by week x sales channel (gross amount, quantity)
SELECT
    strftime('%Y-W%W', o.order_ts) AS sale_week,
    sc.channel_code,
    SUM(CAST(oi.gross_amount AS REAL)) AS gross_amount,
    SUM(oi.quantity) AS quantity
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN sales_channels sc ON sc.channel_id = o.channel_id
GROUP BY sale_week, sc.channel_code
ORDER BY sale_week, sc.channel_code
LIMIT 20;

-- R2: Feb 2020 vs Feb 2019, filtered by reseller and event type
SELECT
    strftime('%Y', o.order_ts) AS yr,
    e.event_type,
    o.reseller_id,
    SUM(CAST(oi.gross_amount AS REAL)) AS gross_amount
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
JOIN events e ON e.event_id = oi.event_id
WHERE strftime('%m', o.order_ts) = '02'
  AND strftime('%Y', o.order_ts) IN ('2019', '2020')
GROUP BY yr, e.event_type, o.reseller_id
ORDER BY e.event_type, yr;

-- R3: Commission rate vs sales results (rate and amount per line, reseller)
SELECT
    o.reseller_id,
    oi.commission_rate,
    SUM(oi.commission_amount) AS total_commission,
    SUM(CAST(oi.gross_amount AS REAL)) AS gross_amount
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.reseller_id IS NOT NULL
GROUP BY o.reseller_id, oi.commission_rate
ORDER BY total_commission DESC
LIMIT 20;

-- R4: Most popular tickets per region (ticket type, region, quantity)
SELECT
    v.region,
    tt.ticket_type_name,
    SUM(oi.quantity) AS quantity
FROM order_items oi
JOIN events e ON e.event_id = oi.event_id
JOIN venues v ON v.venue_id = e.venue_id
JOIN ticket_types tt ON tt.ticket_type_id = oi.ticket_type_id
GROUP BY v.region, tt.ticket_type_name
ORDER BY v.region, quantity DESC
LIMIT 20;
