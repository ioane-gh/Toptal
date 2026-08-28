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

## Other decisions

- Money fields use `decimal.Decimal` throughout Python and `DECIMAL(18,4)`
  in SQL Server; `commission_rate` uses `DECIMAL(9,6)`. Never `float`.
- `config.database.project_db_name` (config.yaml) is the safety check for
  `make reset`: it refuses to drop anything unless `.env`'s `MSSQL_DB`
  matches this value, so a misconfigured `.env` can't point `reset` at an
  unrelated database.
