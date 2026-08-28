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

## Other decisions

- Money fields use `decimal.Decimal` throughout Python and `DECIMAL(18,4)`
  in SQL Server; `commission_rate` uses `DECIMAL(9,6)`. Never `float`.
- `config.database.project_db_name` (config.yaml) is the safety check for
  `make reset`: it refuses to drop anything unless `.env`'s `MSSQL_DB`
  matches this value, so a misconfigured `.env` can't point `reset` at an
  unrelated database.
