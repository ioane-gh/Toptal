"""B2B ingestion: SQLite (data/source/b2b.db) -> SQL Server raw_b2b.*

One job per source table, table-level parallelism via ThreadPoolExecutor.

Full load: truncate target, keyset-paginate the source on its primary key
(never OFFSET), bulk insert one transaction per chunk.

Incremental load: read the watermark, capture high_bound = utcnow() once at
job start, page through `updated_at > wm AND updated_at <= high_bound` (also
keyset-paginated on pk within that filtered window), land each chunk into a
staging table, MERGE into the target on the primary key, and only advance
the watermark to high_bound after every chunk of the job has committed.
A crashed incremental job re-reads safely from the last committed watermark
next run (MERGE is idempotent on pk). A crashed full load is re-run from
the start (truncate + reload) -- a deliberate trade-off, see README.

sales_channels has no updated_at (it's a tiny fixed reference table -- see
NOTES.md) so it is always full-loaded, even during an "incremental" run.
"""
from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Engine

from src.common.config import Settings
from src.common.db import retry_on_transient
from src.common.logging_setup import data_error
from src.common.metadata import JobContext, get_watermark, set_watermark, utcnow
from src.common.signals import STOP_EVENT


@dataclass
class TableSpec:
    pk: str
    columns: list[str]  # sqlite column order == raw_b2b target column order
    types: dict[str, str]  # column -> conversion kind
    has_updated_at: bool = True


TABLE_SPECS: dict[str, TableSpec] = {
    "organizers": TableSpec(
        pk="organizer_id",
        columns=["organizer_id", "organizer_name", "country", "region", "city", "is_active", "created_at", "updated_at"],
        types={"organizer_id": "int", "organizer_name": "str", "country": "str", "region": "str", "city": "str", "is_active": "bool", "created_at": "datetime", "updated_at": "datetime"},
    ),
    "venues": TableSpec(
        pk="venue_id",
        columns=["venue_id", "organizer_id", "venue_name", "city", "region", "country", "capacity", "created_at", "updated_at"],
        types={"venue_id": "int", "organizer_id": "int", "venue_name": "str", "city": "str", "region": "str", "country": "str", "capacity": "int", "created_at": "datetime", "updated_at": "datetime"},
    ),
    "events": TableSpec(
        pk="event_id",
        columns=["event_id", "venue_id", "organizer_id", "event_name", "event_type", "event_date", "status", "created_at", "updated_at"],
        types={"event_id": "int", "venue_id": "int", "organizer_id": "int", "event_name": "str", "event_type": "str", "event_date": "date", "status": "str", "created_at": "datetime", "updated_at": "datetime"},
    ),
    "ticket_types": TableSpec(
        pk="ticket_type_id",
        columns=["ticket_type_id", "event_id", "ticket_type_name", "face_value", "currency", "created_at", "updated_at"],
        types={"ticket_type_id": "int", "event_id": "int", "ticket_type_name": "str", "face_value": "decimal2", "currency": "str", "created_at": "datetime", "updated_at": "datetime"},
    ),
    "resellers": TableSpec(
        pk="reseller_id",
        columns=["reseller_id", "reseller_name", "country", "region", "city", "integration_type", "is_active", "created_at", "updated_at"],
        types={"reseller_id": "int", "reseller_name": "str", "country": "str", "region": "str", "city": "str", "integration_type": "str", "is_active": "bool", "created_at": "datetime", "updated_at": "datetime"},
    ),
    "partnership_agreements": TableSpec(
        pk="agreement_id",
        columns=["agreement_id", "organizer_id", "reseller_id", "commission_rate", "valid_from", "valid_to", "created_at", "updated_at"],
        types={"agreement_id": "int", "organizer_id": "int", "reseller_id": "int", "commission_rate": "decimal6", "valid_from": "date", "valid_to": "date_null", "created_at": "datetime", "updated_at": "datetime"},
    ),
    "customers": TableSpec(
        pk="customer_id",
        columns=["customer_id", "first_name", "last_name", "email", "country", "region", "city", "created_at", "updated_at"],
        types={"customer_id": "int", "first_name": "str", "last_name": "str", "email": "str", "country": "str", "region": "str", "city": "str", "created_at": "datetime", "updated_at": "datetime"},
    ),
    "sales_channels": TableSpec(
        pk="channel_id",
        columns=["channel_id", "channel_code", "channel_name"],
        types={"channel_id": "int", "channel_code": "str", "channel_name": "str"},
        has_updated_at=False,
    ),
    "orders": TableSpec(
        pk="order_id",
        columns=["order_id", "customer_id", "seller_type", "organizer_id", "reseller_id", "channel_id", "order_ts", "currency", "total_amount", "total_quantity", "order_status", "created_at", "updated_at"],
        types={"order_id": "int", "customer_id": "int", "seller_type": "str", "organizer_id": "int", "reseller_id": "int_null", "channel_id": "int", "order_ts": "datetime", "currency": "str", "total_amount": "decimal2", "total_quantity": "int", "order_status": "str", "created_at": "datetime", "updated_at": "datetime"},
    ),
    "order_items": TableSpec(
        pk="order_item_id",
        columns=["order_item_id", "order_id", "event_id", "ticket_type_id", "quantity", "unit_price", "gross_amount", "commission_rate", "commission_amount", "created_at", "updated_at"],
        types={"order_item_id": "int", "order_id": "int", "event_id": "int", "ticket_type_id": "int", "quantity": "int", "unit_price": "decimal2", "gross_amount": "decimal2", "commission_rate": "decimal6", "commission_amount": "decimal2", "created_at": "datetime", "updated_at": "datetime"},
    ),
}


# --------------------------------------------------------------------------
# Row conversion
# --------------------------------------------------------------------------

def convert_value(raw, kind: str):
    if raw is None or raw == "":
        if kind in ("int_null", "date_null"):
            return None
        raise ValueError(f"unexpected NULL/empty for non-nullable field of kind {kind}")
    if kind in ("int", "int_null"):
        return int(raw)
    if kind == "bool":
        return bool(int(raw))
    if kind == "str":
        return str(raw)
    if kind == "decimal2":
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    if kind == "decimal6":
        return Decimal(str(raw)).quantize(Decimal("0.000001"))
    if kind in ("date", "date_null"):
        return datetime.strptime(raw, "%Y-%m-%d").date()
    if kind == "datetime":
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
    raise ValueError(f"unknown conversion kind: {kind}")


def convert_row(spec: TableSpec, row: sqlite3.Row) -> dict:
    return {col: convert_value(row[col], spec.types[col]) for col in spec.columns}


# --------------------------------------------------------------------------
# SQLite readers (keyset pagination -- never OFFSET)
# --------------------------------------------------------------------------

def sqlite_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def read_chunks_full(conn: sqlite3.Connection, table: str, spec: TableSpec, chunk_size: int):
    last_pk = 0
    cols_sql = ", ".join(spec.columns)
    while True:
        if STOP_EVENT.is_set():
            return  # stop scheduling new chunks; whatever was already yielded has committed
        rows = conn.execute(
            f"SELECT {cols_sql} FROM {table} WHERE {spec.pk} > ? ORDER BY {spec.pk} LIMIT ?",
            (last_pk, chunk_size),
        ).fetchall()
        if not rows:
            return
        yield rows
        last_pk = rows[-1][spec.pk]


def read_chunks_incremental(conn: sqlite3.Connection, table: str, spec: TableSpec, wm_iso: str, hb_iso: str, chunk_size: int):
    last_pk = 0
    cols_sql = ", ".join(spec.columns)
    while True:
        if STOP_EVENT.is_set():
            return  # stop scheduling new chunks; the watermark is only advanced after a full pass, so this is safe to resume
        rows = conn.execute(
            f"SELECT {cols_sql} FROM {table} WHERE updated_at > ? AND updated_at <= ? AND {spec.pk} > ? ORDER BY {spec.pk} LIMIT ?",
            (wm_iso, hb_iso, last_pk, chunk_size),
        ).fetchall()
        if not rows:
            return
        yield rows
        last_pk = rows[-1][spec.pk]


# --------------------------------------------------------------------------
# SQL Server writers
# --------------------------------------------------------------------------

def _build_merge_sql(table: str, spec: TableSpec, stg_table: str) -> str:
    non_pk_cols = [c for c in spec.columns if c != spec.pk] + ["_run_id"]
    set_clause = ", ".join(f"tgt.{c} = src.{c}" for c in non_pk_cols)
    insert_cols = spec.columns + ["_run_id"]
    insert_cols_sql = ", ".join(insert_cols)
    insert_vals_sql = ", ".join(f"src.{c}" for c in insert_cols)
    return (
        f"MERGE raw_b2b.{table} AS tgt "
        f"USING raw_b2b.{stg_table} AS src "
        f"ON tgt.{spec.pk} = src.{spec.pk} "
        f"WHEN MATCHED THEN UPDATE SET {set_clause} "
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols_sql}) VALUES ({insert_vals_sql}) "
        f"OUTPUT $action;"
    )


def _ensure_staging_table(engine: Engine, table: str, stg_table: str, spec: TableSpec, logger: logging.Logger) -> None:
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT OBJECT_ID(:n, 'U')"), {"n": f"raw_b2b.{stg_table}"}).scalar()
        if exists:
            return
        cols_sql = ", ".join(spec.columns) + ", _run_id"
        conn.execute(text(f"SELECT TOP 0 {cols_sql} INTO raw_b2b.{stg_table} FROM raw_b2b.{table}"))
        logger.info("Created staging table raw_b2b.%s", stg_table)


def _load_full(engine: Engine, sqlite_conn: sqlite3.Connection, table: str, spec: TableSpec, chunk_size: int, bulk_batch_size: int, max_retries: int, backoff: float, job: JobContext, logger: logging.Logger, run_id: str) -> None:
    with engine.begin() as conn:
        pre_count = conn.execute(text(f"SELECT COUNT(*) FROM raw_b2b.{table}")).scalar()
        logger.info("Pre-truncate count for raw_b2b.%s: %s", table, pre_count, extra={"job": job.job_name})
        conn.execute(text(f"TRUNCATE TABLE raw_b2b.{table}"))

    metadata = MetaData(schema="raw_b2b")
    tbl = Table(table, metadata, autoload_with=engine)

    for chunk in read_chunks_full(sqlite_conn, table, spec, chunk_size):
        job.rows_read += len(chunk)
        good_rows = []
        for row in chunk:
            try:
                converted = convert_row(spec, row)
                converted["_run_id"] = run_id
                good_rows.append(converted)
            except Exception as exc:  # noqa: BLE001
                job.rows_skipped += 1
                data_error(
                    logger, source=f"b2b.{table}", row=row[spec.pk], reason="convert_error",
                    detail=str(exc), payload=dict(row), run_id=run_id, job=job.job_name,
                )
        if not good_rows:
            continue

        @retry_on_transient(max_retries=max_retries, backoff_sec=backoff)
        def _do(rows=good_rows):
            with engine.begin() as conn:
                for i in range(0, len(rows), bulk_batch_size):
                    conn.execute(tbl.insert(), rows[i : i + bulk_batch_size])

        _do()
        job.rows_inserted += len(good_rows)


def _load_incremental(engine: Engine, sqlite_conn: sqlite3.Connection, table: str, spec: TableSpec, chunk_size: int, bulk_batch_size: int, max_retries: int, backoff: float, job: JobContext, logger: logging.Logger, run_id: str) -> None:
    wm = get_watermark(engine, "b2b", table)
    high_bound = utcnow()
    wm_iso = wm.strftime("%Y-%m-%dT%H:%M:%SZ")
    hb_iso = high_bound.strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info("Incremental window for b2b.%s: (%s, %s]", table, wm_iso, hb_iso, extra={"job": job.job_name})

    stg_table = f"stg_{table}"
    _ensure_staging_table(engine, table, stg_table, spec, logger)
    merge_sql = _build_merge_sql(table, spec, stg_table)

    metadata = MetaData(schema="raw_b2b")
    stg_tbl = Table(stg_table, metadata, autoload_with=engine)

    for chunk in read_chunks_incremental(sqlite_conn, table, spec, wm_iso, hb_iso, chunk_size):
        job.rows_read += len(chunk)
        good_rows = []
        for row in chunk:
            try:
                converted = convert_row(spec, row)
                converted["_run_id"] = run_id
                good_rows.append(converted)
            except Exception as exc:  # noqa: BLE001
                job.rows_skipped += 1
                data_error(
                    logger, source=f"b2b.{table}", row=row[spec.pk], reason="convert_error",
                    detail=str(exc), payload=dict(row), run_id=run_id, job=job.job_name,
                )
        if not good_rows:
            continue

        @retry_on_transient(max_retries=max_retries, backoff_sec=backoff)
        def _do(rows=good_rows):
            with engine.begin() as conn:
                conn.execute(text(f"TRUNCATE TABLE raw_b2b.{stg_table}"))
                for i in range(0, len(rows), bulk_batch_size):
                    conn.execute(stg_tbl.insert(), rows[i : i + bulk_batch_size])
                result = conn.execute(text(merge_sql))
                actions = [r[0] for r in result.fetchall()]
                return actions

        actions = _do()
        job.rows_inserted += sum(1 for a in actions if a == "INSERT")
        job.rows_updated += sum(1 for a in actions if a == "UPDATE")

    # Only advance the watermark once every chunk of this job has committed.
    set_watermark(engine, "b2b", table, "updated_at", high_bound, run_id)


def ingest_table(settings: Settings, engine: Engine, run_id: str, table: str, mode: str, logger: logging.Logger) -> None:
    spec = TABLE_SPECS[table]
    cfg = settings.get("ingestion.b2b")
    chunk_size = cfg["chunk_size"]
    bulk_batch_size = cfg["bulk_batch_size"]
    max_retries = cfg["max_retries"]
    backoff = cfg["retry_backoff_sec"]
    max_skip_ratio = cfg["max_skip_ratio"]

    effective_mode = "FULL" if (mode == "FULL" or not spec.has_updated_at) else "INCREMENTAL"

    job = JobContext(engine, run_id, f"ingest_b2b_{table}", "b2b", table, f"raw_b2b.{table}", effective_mode, logger=logger)
    with job:
        sqlite_conn = sqlite_connect(settings.path("sqlite_db"))
        try:
            if effective_mode == "FULL":
                _load_full(engine, sqlite_conn, table, spec, chunk_size, bulk_batch_size, max_retries, backoff, job, logger, run_id)
            else:
                _load_incremental(engine, sqlite_conn, table, spec, chunk_size, bulk_batch_size, max_retries, backoff, job, logger, run_id)
        finally:
            sqlite_conn.close()

        if job.rows_read > 0 and (job.rows_skipped / job.rows_read) > max_skip_ratio:
            raise RuntimeError(
                f"skip ratio {job.rows_skipped}/{job.rows_read} exceeds max_skip_ratio={max_skip_ratio} for b2b.{table}"
            )


def run_b2b_ingestion(settings: Settings, engine: Engine, run_id: str, mode: str, tables_filter: Optional[list[str]], logger: logging.Logger) -> list[str]:
    """Runs one job per table, table-level parallel via ThreadPoolExecutor,
    submitted in the configured load_order (dimensions before facts).
    Returns the list of tables that were processed.
    """
    load_order = settings.get("ingestion.b2b.load_order")
    workers = settings.get("ingestion.b2b.workers")
    tables = [t for t in load_order if not tables_filter or t in tables_filter]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(ingest_table, settings, engine, run_id, table, mode, logger): table for table in tables}
        for future in as_completed(futures):
            table = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 -- JobContext already recorded this; nothing more to do
                logger.error("Unexpected error outside JobContext for table %s: %s", table, exc)

    return tables
