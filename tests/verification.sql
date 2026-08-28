-- End-to-end verification queries for the demo sequence in the README
-- ("Acceptance criteria -- end-to-end demo"). Run against the target SQL
-- Server database after `make ingest-full` and `make ingest-incr` (and,
-- separately, sqlite3 against data/source/b2b.db for the SQLite-side counts
-- in section 1). Each numbered section corresponds to one Phase 8 check.

-- ============================================================
-- 1. Row-count reconciliation: raw_b2b vs SQLite, raw_reseller vs valid CSV rows
--    (Run the SQLite half separately: `sqlite3 data/source/b2b.db
--    "SELECT 'orders', COUNT(*) FROM orders UNION ALL SELECT 'order_items', COUNT(*) FROM order_items;"`
--    and diff by hand against the counts below -- SQL Server and SQLite
--    can't be queried in one statement.)
-- ============================================================
SELECT 'venues' AS tbl, COUNT(*) AS raw_b2b_count FROM raw_b2b.venues
UNION ALL SELECT 'events', COUNT(*) FROM raw_b2b.events
UNION ALL SELECT 'ticket_types', COUNT(*) FROM raw_b2b.ticket_types
UNION ALL SELECT 'resellers', COUNT(*) FROM raw_b2b.resellers
UNION ALL SELECT 'partnership_agreements', COUNT(*) FROM raw_b2b.partnership_agreements
UNION ALL SELECT 'sales_channels', COUNT(*) FROM raw_b2b.sales_channels
UNION ALL SELECT 'orders', COUNT(*) FROM raw_b2b.orders
UNION ALL SELECT 'order_items', COUNT(*) FROM raw_b2b.order_items;

SELECT SUM(total_amount) AS raw_b2b_orders_total_amount FROM raw_b2b.orders;

SELECT COUNT(*) AS raw_reseller_daily_sales_count FROM raw_reseller.daily_sales;

SELECT SUM(row_count) AS csv_total_rows, SUM(rows_inserted) AS csv_total_valid_rows, SUM(rows_skipped) AS csv_total_defect_rows
FROM meta.processed_file WHERE status = 'PROCESSED';

-- ============================================================
-- 2. meta.ingestion_run shows two runs (full, incremental) with correct
--    types, timings, statuses
-- ============================================================
SELECT run_id, run_type, source_filter, started_at, finished_at,
       DATEDIFF(SECOND, started_at, finished_at) AS duration_sec,
       status, rows_inserted, rows_skipped, log_file
FROM meta.ingestion_run
ORDER BY started_at DESC;

-- ============================================================
-- 3. meta.ingestion_job counters sum to the run totals
-- ============================================================
SELECT r.run_id, r.run_type, r.rows_inserted AS run_rows_inserted, r.rows_skipped AS run_rows_skipped,
       SUM(j.rows_inserted) AS jobs_rows_inserted, SUM(j.rows_skipped) AS jobs_rows_skipped
FROM meta.ingestion_run r
JOIN meta.ingestion_job j ON j.run_id = r.run_id
GROUP BY r.run_id, r.run_type, r.rows_inserted, r.rows_skipped
ORDER BY r.run_id;

-- ============================================================
-- 4. The incremental run's rows_read is a small fraction of the full run's
-- ============================================================
SELECT run_type, SUM(rows_read) AS total_rows_read
FROM meta.ingestion_job j
JOIN meta.ingestion_run r ON r.run_id = j.run_id
GROUP BY run_type, r.started_at
ORDER BY r.started_at;

-- ============================================================
-- 5. No duplicates in any raw table (natural key uniqueness)
-- ============================================================
SELECT 'orders' AS tbl, order_id AS pk, COUNT(*) AS n FROM raw_b2b.orders GROUP BY order_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'order_items', order_item_id, COUNT(*) FROM raw_b2b.order_items GROUP BY order_item_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'daily_sales_file_row', CAST(NULL AS INT), COUNT(*)
FROM raw_reseller.daily_sales GROUP BY _source_file_name, _source_row_number HAVING COUNT(*) > 1;
-- Expect zero rows from all three unions.

-- ============================================================
-- 6. Skipped rows are traceable: see logs/pipeline_<run_id>.log, grep
--    "DATA_ERROR" and group by "reason=" -- every defect type from
--    config.generation.defects should appear at least once. Example:
--      grep DATA_ERROR logs/pipeline_*.log | grep -oP 'reason=\K[a-z_]+' | sort | uniq -c
-- ============================================================
SELECT status, COUNT(*) AS file_count, SUM(rows_skipped) AS total_rows_skipped
FROM meta.processed_file
GROUP BY status;

-- ============================================================
-- 7. R1-R4 against the raw layer (B2B side; join to partnership_agreements
--    for the reseller/CSV side of R2-R3 -- see NOTES.md for the join path)
-- ============================================================

-- R1: Sales by week x sales channel
SELECT
    DATEPART(ISO_WEEK, o.order_ts) AS iso_week, DATEPART(YEAR, o.order_ts) AS yr,
    sc.channel_code,
    SUM(oi.gross_amount) AS gross_amount,
    SUM(oi.quantity) AS quantity
FROM raw_b2b.orders o
JOIN raw_b2b.order_items oi ON oi.order_id = o.order_id
JOIN raw_b2b.sales_channels sc ON sc.channel_id = o.channel_id
GROUP BY DATEPART(ISO_WEEK, o.order_ts), DATEPART(YEAR, o.order_ts), sc.channel_code
ORDER BY yr, iso_week, sc.channel_code;

-- R2: Feb 2020 vs Feb 2019, filtered by reseller and event type
SELECT YEAR(o.order_ts) AS yr, e.event_type, o.reseller_id, SUM(oi.gross_amount) AS gross_amount
FROM raw_b2b.orders o
JOIN raw_b2b.order_items oi ON oi.order_id = o.order_id
JOIN raw_b2b.events e ON e.event_id = oi.event_id
WHERE MONTH(o.order_ts) = 2 AND YEAR(o.order_ts) IN (2019, 2020)
GROUP BY YEAR(o.order_ts), e.event_type, o.reseller_id
ORDER BY e.event_type, yr;

-- R3: Commission rate vs sales results
SELECT o.reseller_id, oi.commission_rate,
       SUM(oi.commission_amount) AS total_commission, SUM(oi.gross_amount) AS gross_amount
FROM raw_b2b.orders o
JOIN raw_b2b.order_items oi ON oi.order_id = o.order_id
WHERE o.reseller_id IS NOT NULL
GROUP BY o.reseller_id, oi.commission_rate
ORDER BY total_commission DESC;

-- R4: Most popular tickets per region
SELECT v.region, tt.ticket_type_name, SUM(oi.quantity) AS quantity
FROM raw_b2b.order_items oi
JOIN raw_b2b.events e ON e.event_id = oi.event_id
JOIN raw_b2b.venues v ON v.venue_id = e.venue_id
JOIN raw_b2b.ticket_types tt ON tt.ticket_type_id = oi.ticket_type_id
GROUP BY v.region, tt.ticket_type_name
ORDER BY v.region, quantity DESC;
