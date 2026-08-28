# Decisions log and open questions

This file records ambiguities in the spec and the decision made, plus other
notable build-time choices, in the order they came up.

## Build environment

This project was built in a sandboxed dev container with **no reachable SQL
Server instance** and no Microsoft ODBC Driver 17 installed (`apt` has no
route to Microsoft's package repo from this sandbox; `pyodbc.drivers()`
returns `[]`). Per Phase 1's acceptance criteria, this was surfaced
immediately rather than papered over.

Practical effect: everything in Phases 1-3 (config, logging, the SQLite
generator, the CSV generator) was built and verified by actually running it
in this environment. Everything from Phase 4 onward that talks to SQL Server
(DDL, ingestion, Great Expectations, the runner, the end-to-end demo) was
built to the spec and is believed correct against SQLAlchemy 2.x /
pyodbc 5.x semantics, but **could not be executed against a live database
here**. `sql/00_create_database.sql` and `src/common/init_db.py` in
particular need a real run against SQL Server + Driver 17 to confirm the
placeholder substitution and batch splitting behave as intended. The README
flags this and gives the commands a reviewer with a real instance should run
to close the loop.

## Phase 2 — Feb 2020 vs Feb 2019 (R2)

`generate_b2b.py` seeds a deliberate year-over-year difference: February
2020 order volume is generated at roughly 1.6x the February 2019 rate (on
top of the general seasonality curve), concentrated in the `CONCERT` and
`FESTIVAL` event types. A reviewer running the R2 query should see Feb 2020
gross sales clearly higher than Feb 2019 for those event types, and roughly
flat for others. Exact counts depend on `run.seed` and `run.profile` and are
printed by `generate_b2b.py` at generation time.

## Phase 3 — commission rate in reseller files

Commission rate is deliberately **absent** from
`config/reseller_file_schema.yaml` / the generated CSVs. Third-party
resellers' commission terms live only in `partnership_agreements` in the
B2B database (every reseller, including third-party ones, has agreement
rows — see Phase 2 rule "resellers with `integration_type='THIRD_PARTY'`
have agreements but no orders"). R3 (commission rate vs. sales results) for
CSV-sourced sales is answered downstream by joining
`raw_reseller.daily_sales.reseller_id` to
`raw_b2b.partnership_agreements.reseller_id` (matched on the sale date
falling inside `[valid_from, valid_to]`), not by carrying a rate in the file
itself.

## Phase 3 — third-party universe and gap days

Third-party resellers' events and customers are generated as their own
namespace (`TP-EVT-######`, `TP-CUST-######`) — never the B2B DB's own
`event_id`/`customer_id` space — reflecting that these belong to an
external reseller's system, not the platform. `RESELLER_ID` and
`RESELLER_NAME` in the files are the real values from `data/source/b2b.db`
so the R3 join works. Each third-party reseller files on ~70% of days
(`FILE_PROBABILITY`), so gaps are the norm, not the exception — ingestion
must not treat a missing day as an error.

Verified manually (see commit history): joining
`raw_reseller.daily_sales.reseller_id` / `sale_date` to
`partnership_agreements(reseller_id, valid_from, valid_to)` resolves a
commission rate for effectively every valid CSV row (1390/1391 rows in a
20-file sample; the one miss was a deliberately injected defect row).

## Phase 5 — sales_channels has no updated_at

`sales_channels` is the one B2B table without `created_at`/`updated_at`
(Phase 2's table spec lists only `channel_id, channel_code, channel_name`),
so it can't participate in watermark-based incremental loads. It's a tiny,
effectively-static reference table (5 rows), so `ingest_b2b.py` always
full-loads it (truncate + reload), even during an `--mode incremental` run
-- see `TableSpec.has_updated_at` / `ingest_table()` in
`src/ingestion/ingest_b2b.py`.

## Phase 6 — pandas vs csv module for row parsing

The spec suggests `pandas.read_csv(..., chunksize=...)`. In practice, pandas'
bad-line handling doesn't fit this job: a genuinely short row (fewer fields
than the header) is silently zero/NaN-padded rather than reported, so it's
indistinguishable from a deliberately blank trailing field, and even with
`keep_default_na=False` (verified empirically) the padded NaN and the real
empty string `""` differ, but only defensively -- while `on_bad_lines`
callbacks only fire for *too many* fields, and only with `engine='python'`.
Reconstructing an accurate physical `_source_row_number` for a line that
pandas silently dropped is not exposed at all.

`ingest_reseller.py` instead reads with the stdlib `csv` module and batches
`csv_chunk_size` rows at a time by hand -- functionally identical to
`chunksize` for the bounded-memory requirement (the file is still never
materialized in full; verified under `--profile-memory` on the 2M-row large
file), but it gives an exact field count per row (so `wrong_column_count`
is caught directly, in both the too-few and too-many direction) and an
exact 1-based row number for every log line.

Encoding detection samples only the first 64KB of the file (`sniff_encoding`)
rather than reading it whole, to keep the "never read a whole file into
memory" guarantee even for encoding sniffing.

One `meta.ingestion_job` row is written per **file** (not one per reseller
or one for the whole reseller source) -- source_object = the file name --
which gives the same fine-grained traceability Phase 5 gets per table.

## Phase 7 — Great Expectations API verification and design notes

The installed version is `great_expectations==1.21.0` (pinned range
`>=1.3,<2` per the spec). Its fluent 1.x API was exercised interactively
against a scratch SQLite database standing in for SQL Server (GX's
`add_table_asset`/`add_query_asset` eagerly test the connection, so this
was the only way to verify it without a live instance -- see "Build
environment" above) before being written into `src/quality/`:
`ctx.data_sources.add_sql(connection_string=...)`, `add_table_asset` /
`add_query_asset`, `add_batch_definition_whole_table`, `gx.ExpectationSuite`
+ `add_expectation(..., meta={"severity": ...})`, `gx.ValidationDefinition`,
`gx.Checkpoint`, and `checkpoint.run()` whose per-expectation results expose
`.success`, `.expectation_config.type/.kwargs/.meta`, and `.result` (with
`unexpected_count` etc). A full mimic run (fake orders/order_items/
daily_sales tables with a deliberately negative `total_amount`, an orphaned
`order_item`, and a duplicate `(_source_file_name, _source_row_number)`)
correctly failed exactly those three expectations and passed the rest,
confirming the whole checkpoint-to-`meta.data_quality_result` pipeline
before it could be tried against the real target. `tests/test_run_validations.py`
carries this as a live (skip-guarded) test since it needs schema-qualified
tables GX's fluent API doesn't support against SQLite.

**The "no orphans" and "known reseller_id" checks aren't single-table
column expectations**, so they don't fit inside `raw_b2b_order_items`'s or
`raw_reseller_daily_sales`'s own suite object:
- Orphan `order_items` (no matching `orders.order_id`) is implemented as its
  own query asset (`order_items LEFT JOIN orders WHERE orders.order_id IS
  NULL`) validated with `ExpectTableRowCountToBeBetween(max_value=0)`, under
  its own suite name `raw_b2b_order_items_orphan_check` -- GX suites are
  keyed uniquely by name, so it can't share `raw_b2b_order_items`'s name.
  `meta.data_quality_result` therefore has four suite_names, not three; the
  three from the spec are still all present and independently queryable.
- `_file_reseller_id matches a known reseller` **is** expressible as a
  plain `ExpectColumnValuesToBeInSet`, so it stays inside
  `raw_reseller_daily_sales`'s own suite -- the allowed set is just fetched
  from `raw_b2b.resellers` at suite-build time rather than hardcoded.

`raw_b2b_orders`'s `order_ts` range check upper-bounds at "tomorrow", not
the static `generation.date_range.end` (2020-12-31): `mutate_b2b.py` and
incremental loads add orders dated at generation time, which are legitimately
outside the historical range.

## Phase 8 — SIGINT/SIGTERM handling scope

`src/common/signals.py` exposes a process-wide `threading.Event`
(`STOP_EVENT`), set by `runner.py`'s signal handlers. `ingest_b2b.py`'s
chunk generators and `ingest_reseller.py`'s row-batching generator check it
between chunks/rows and stop yielding new work, so whatever chunk already
started committing finishes normally and nothing new begins -- exactly
"stop scheduling new chunks, let in-flight chunks commit."

`ProcessPoolExecutor` workers (reseller file processing) are separate OS
processes and don't share that in-process `Event`, so a signal doesn't
reach a file already dispatched to a worker -- it finishes that file
normally. What the runner *does* stop is handing out new files: the
submission loop checks `STOP_EVENT` before each `executor.submit(...)` call.
Given files are the unit of work there (typically small, except the one
oversized demo file), this is the right granularity trade-off rather than
plumbing a `multiprocessing.Event` through every worker for finer-grained
mid-file cancellation.

## Phase 9 — dbt skeleton and requirements.txt

`dbt-sqlserver` is not in the spec's Appendix A pin list (only `dbt-sqlserver`
is named in the stack table, and Appendix A's `requirements.txt` block omits
it entirely), but Phase 9 explicitly requires `dbt debug` to pass, which
needs the package installed. Added `dbt-sqlserver==1.11.1` (pulls in
`dbt-core`) to `requirements.txt` as a deliberate, documented addition.

**Post-hoc fix:** the original pin of `pyodbc==5.1.0` (Appendix A's own
pinned version) conflicts with `dbt-sqlserver==1.11.1`, which requires
`pyodbc>=5.2.0` -- `pip install -r requirements.txt` fails outright with
`ResolutionImpossible` (reported by a user running the Windows quick-start
steps; nothing installs, hence every subsequent `ModuleNotFoundError`).
Bumped to `pyodbc==5.2.0`, the lowest version that satisfies both. This
sandbox has no ODBC driver either way, so the conflict wasn't visible while
building Phases 1-9 (pip was never run against the full, final
`requirements.txt` with dbt-sqlserver already present) -- caught and fixed
only once exercised on a real Windows machine. Verified with a clean venv:
`pip install -r requirements.txt` now resolves and installs cleanly
(`dbt-core` lands on 1.11.14 rather than 1.12.3, still within
`dbt-sqlserver`'s `>=1.11.0,<2.0` constraint).

Verified in this sandbox: `dbt debug` correctly reports `profiles.yml file
[OK found and valid]` and `dbt_project.yml file [OK found and valid]` for
`dbt/profiles.yml.example` + `dbt/dbt_project.yml` (fields checked directly
against the installed `SQLServerCredentials` dataclass -- `driver`, `server`,
`database`, `schema`, `windows_login`, `encrypt`, `trust_cert`, etc.); it
then fails only at the live connection test, for the same reason everything
else SQL-Server-side does here. `dbt parse` succeeds cleanly against
`dbt/models/sources/sources.yml` (0 models, as required -- Phase 9 stops at
"declare sources," writes no models). The one example snapshot was
originally saved as `organizers_snapshot.sql.example` (not `.sql`) so dbt's
snapshot parser doesn't pick up a snapshot with no corresponding source
data guarantees this early -- exactly the "one commented example" the spec
asks for. It's since been replaced with `venues_snapshot.sql.example` (see
"Removing organizers and customers" below) once `organizers` itself was
removed.

## Removing organizers and customers

Post-Phase-9, working through what a `dwh`/`datamart` build actually needs
against the real report requirements (sales by week × channel; Feb 2020 vs.
2019 filtered by reseller and event type; commission rate vs. sales
results; most popular tickets per region) surfaced that **no requirement
reads a customer attribute at all, and none reads an organizer's
descriptive attributes** (name, country, `is_active`) -- only
`organizer_id` as a bare grouping key for the commission-rate report. Asked
explicitly how far to take it, the call was: remove both entirely,
including `organizer_id`/`customer_id` as bare columns, not just the
`organizers`/`customers` tables -- accepting that the commission-rate
report now only breaks down by reseller, not by organizer.

**Scope of the cut**, end to end:
- `organizers` and `customers` tables dropped entirely from
  `src/generators/b2b_schema.py` (SQLite) and `sql/03_raw_b2b_tables.sql`
  (SQL Server).
- `organizer_id` removed from `venues`, `events`, `orders`, and
  `partnership_agreements`; `customer_id` removed from `orders`.
- `raw_reseller.daily_sales` and the CSV contract
  (`config/reseller_file_schema.yaml`) lose `CUSTOMER_ID`/`CUSTOMER_EMAIL`/
  `CUSTOMER_COUNTRY`/`CUSTOMER_CITY` too, for the same reason -- consistency
  across both sources, not just the B2B side.
- `ingest_b2b.py`'s `TABLE_SPECS` and `config.yaml`'s `ingestion.b2b.load_order`
  updated to match; `ingest_reseller.py` needed no code changes at all
  (it reads column layout entirely from the schema YAML, no hardcoded
  column names).
- dbt: `sources.yml` no longer lists the two dropped tables; the now-broken
  `customers_snapshot.sql` (a real, non-`.example` file we'd built together
  a few turns earlier) is deleted outright rather than left dangling.

**A structural consequence worth flagging:** order items used to be
constrained to "any event under the same organizer" (multiple events could
share one order via their common organizer). With no organizer grouping
left, an order is now scoped to a single event -- picked once per order,
all its line items drawn from that event's own ticket types. This
incidentally also removes a currency-consistency risk: currency is
assigned per event, so items sharing one order always share one currency
by construction, where previously a multi-event order under one organizer
relied on that organizer having a single consistent currency across all its
events.

This is additive-only DDL (`IF NOT EXISTS`), like the rest of `init_db.py`
-- it does not `ALTER`/`DROP` an already-deployed database. On a database
created before this change, `make reset && make init-db` is the supported
path to a clean schema; there's no in-place column-migration script (out of
scope for a demo-grade pipeline that owns and fully regenerates its own
source data).

## Choosing which tables to snapshot

Working through which `raw_b2b` tables need their own dbt SCD2 snapshot
(vs. a plain point-in-time join, vs. nothing at all) against the same four
report requirements:

- **`venues` is the one *conceptually* plausible candidate -- and it still
  doesn't get a real snapshot.** "Most popular tickets per region" groups
  by `venues.region`, a descriptive attribute rather than a stable key, so
  in principle a plain current-state join could misattribute historical
  sales if a region were ever corrected. In practice: nothing in this
  pipeline ever updates a venue after creation --
  `src/generators/mutate_b2b.py` only touches `orders`/`order_items`, never
  any dimension table -- so a real `venues_snapshot` would have
  `dbt_valid_to` permanently `NULL` for every row: syntactically correct,
  empirically inert, since the source it watches never changes. Caught
  after already recommending it and getting corrected. The built models
  (`dbt/models/dwh/`) use a plain current-state join for `region` instead;
  `dbt/snapshots/venues_snapshot.sql.example` stays as a `.example` purely
  to show the SCD2 mechanic, with this caveat spelled out in its own
  comment.
- **`resellers`/`event_type`/`ticket_type`/`channel` don't need SCD2.**
  The reports filter/group by `reseller_id` (a stable key -- an ID doesn't
  drift even if the reseller's other attributes do) and `event_type`
  (fixed at event creation). A plain current-state lookup (for
  `reseller_name` display) or a denormalized join at staging time is
  enough; there's no point-in-time-correctness gap to close.
- **`partnership_agreements` is already natively versioned** by the source
  itself (`valid_from`/`valid_to`, multiple rows per reseller) --
  `commission_rate` is baked into `order_items` at sale time, not resolved
  by joining to a current-state dimension. Snapshotting an
  already-historized table would just add a second, competing validity
  concept (`dbt_valid_from` vs. the table's own `valid_from`).
- One structural point that shapes the whole `dwh` design, not just
  snapshots: ~40% of resellers are `THIRD_PARTY` and have sales *only* in
  `raw_reseller.daily_sales`, never in `raw_b2b.orders`. A fact table built
  off `raw_b2b.order_items` alone would silently exclude that entire slice
  from every report that mentions "resellers" or "regions." Both sources
  need their own staging model, unioned into one fact table, before any of
  the four reports are fully correct.

## Building the real dwh/datamart models

Eight models, no snapshot in the active set (see the correction above):

- `dwh/staging/stg_b2b_sales.sql`, `stg_reseller_sales.sql` (views) --
  one per source, each resolving that source's own display attributes
  (channel, event type, region, ticket type name) and commission. The
  reseller side casts `raw_reseller.daily_sales`'s NVARCHAR(255) columns
  and resolves `commission_rate`/`commission_amount` via
  `partnership_agreements` (neither exists in the CSV).
- `dwh/dim_resellers.sql`, `dwh/fact_sales.sql` (tables) -- the plain
  reseller lookup and the `UNION ALL` of both staging models, at
  ticket-sold grain. Every datamart model reads from `fact_sales` only.
- `datamart/*.sql` (tables, `+schema: datamart`) -- one model per report
  requirement: `sales_by_week_channel` (R1), `yoy_feb_by_reseller_event_type`
  (R2), `commission_vs_sales` (R3), `popular_tickets_by_region` (R4).

Verified in this sandbox with `dbt parse` (8 models, 8 data tests, 14
sources -- all resolve) and `dbt compile` (fails only at the live
connection step, same as everywhere else SQL-Server-side; the model DAG
itself -- `ref()`/`source()` resolution, schema.yml tests -- is confirmed
correct up to that point). Column names in `stg_reseller_sales.sql` were
cross-checked against `config/reseller_file_schema.yaml`'s `target_column`
values directly, not just inferred, since a typo there wouldn't surface
until an actual run against a live database.

## Other decisions

- Money fields use `decimal.Decimal` throughout Python and `DECIMAL(18,4)`
  in SQL Server; `commission_rate` uses `DECIMAL(9,6)`. Never `float`.
- `config.database.project_db_name` (config.yaml) is the safety check for
  `make reset`: it refuses to drop anything unless `.env`'s `MSSQL_DB`
  matches this value, so a misconfigured `.env` can't point `reset` at an
  unrelated database.
