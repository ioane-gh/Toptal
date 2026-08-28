"""Reseller ingestion: daily-sales CSV files (data/reseller/) -> SQL Server
raw_reseller.daily_sales.

Files are immutable external artifacts with no updated_at -- incrementality
is "have I already processed this file?", answered from meta.processed_file
(see discover/decide/claim/finalize below). Files are never moved.

Row-level parsing deliberately uses Python's csv module with manual
chunk-batching rather than pandas.read_csv (see NOTES.md "Phase 6 --
pandas vs csv module"): it gives exact physical row numbers for every row
(needed for _source_row_number and for pinpointing bad rows in the log) and
reports a genuinely short row as its own distinct "wrong_column_count"
defect rather than silently zero-padding it, while remaining just as
memory-bounded as pandas' chunksize (rows are batched csv_chunk_size at a
time, the file is never read in full).
"""
from __future__ import annotations

import csv
import logging
import os
import re
import tracemalloc
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Engine

from src.common.config import Settings, get_settings
from src.common.db import get_engine, retry_on_transient
from src.common.logging_setup import data_error, get_logger
from src.common.metadata import JobContext, claim_file, complete_file, fail_file, is_file_processed, mark_skipped_invalid_name


def load_schema(settings: Settings) -> dict:
    path = settings.repo_root / "config" / "reseller_file_schema.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class RowValidationError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass
class FileCandidate:
    path: Path
    file_name: str
    valid_name: bool
    reseller_id: Optional[str] = None
    sale_date: Optional[date] = None


def discover_files(reseller_dir: Path, schema: dict) -> list[FileCandidate]:
    pattern = re.compile(schema["file_name_pattern"], re.IGNORECASE)
    date_format = schema["file_name_date_format"]
    candidates = []
    for path in sorted(reseller_dir.iterdir()):
        if not path.is_file():
            continue
        m = pattern.match(path.name)
        if not m:
            candidates.append(FileCandidate(path=path, file_name=path.name, valid_name=False))
            continue
        mm, dd, yyyy, reseller_id = m.groups()
        try:
            sale_date = datetime.strptime(f"{mm}{dd}{yyyy}", date_format).date()
        except ValueError:
            candidates.append(FileCandidate(path=path, file_name=path.name, valid_name=False))
            continue
        candidates.append(FileCandidate(path=path, file_name=path.name, valid_name=True, reseller_id=reseller_id, sale_date=sale_date))
    return candidates


def sniff_encoding(path: Path, fallbacks: list[str]) -> str:
    """Peeks at the first 64KB (never the whole file) to pick an encoding."""
    sample = path.read_bytes()[:65536]
    for enc in fallbacks:
        try:
            sample.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return fallbacks[-1]


def _parse_date(value: str, fmt: str) -> None:
    datetime.strptime(value, fmt)


def validate_and_build_row(
    raw_row: list[str],
    row_number: int,
    columns: list[dict],
    seen_ticket_ids: set[str],
    schema: dict,
    tolerance: Decimal,
) -> dict:
    if len(raw_row) != len(columns):
        raise RowValidationError("wrong_column_count", f"expected {len(columns)} fields, got {len(raw_row)}")

    row = {c["name"]: v.strip() if isinstance(v, str) else v for c, v in zip(columns, raw_row)}

    for col in columns:
        if not col["nullable"] and row[col["name"]] == "":
            raise RowValidationError("missing_required", f"{col['name']} is required")

    ticket_id = row["TICKET_ID"]
    if ticket_id in seen_ticket_ids:
        raise RowValidationError("duplicate_ticket_id", f"TICKET_ID {ticket_id} repeats in this file")

    try:
        _parse_date(row["EVENT_DATE"], "%Y-%m-%d")
        _parse_date(row["SALE_DATE"], "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise RowValidationError("bad_date", str(exc)) from exc

    try:
        quantity = int(row["QUANTITY"])
    except ValueError as exc:
        raise RowValidationError("bad_number", f"QUANTITY not numeric: {row['QUANTITY']!r}") from exc
    if quantity <= 0:
        raise RowValidationError("negative_quantity", f"QUANTITY={quantity}")

    try:
        unit_price = Decimal(row["UNIT_PRICE"])
        total_amount = Decimal(row["TOTAL_AMOUNT"])
    except InvalidOperation as exc:
        raise RowValidationError("bad_number", f"non-numeric price: UNIT_PRICE={row['UNIT_PRICE']!r} TOTAL_AMOUNT={row['TOTAL_AMOUNT']!r}") from exc

    if abs(total_amount - (unit_price * quantity)) > tolerance:
        raise RowValidationError("amount_mismatch", f"TOTAL_AMOUNT={total_amount} != QUANTITY*UNIT_PRICE={unit_price * quantity}")

    if row["CURRENCY"] not in schema["allowed_currencies"]:
        raise RowValidationError("invalid_currency", f"CURRENCY={row['CURRENCY']!r}")

    seen_ticket_ids.add(ticket_id)
    return row


def _target_row(row: dict, columns: list[dict], file_name: str, row_number: int, file_reseller_id: str, file_sale_date: date, run_id: str) -> dict:
    out = {col["target_column"]: row[col["name"]] for col in columns}
    out["_source_file_name"] = file_name
    out["_source_row_number"] = row_number
    out["_file_reseller_id"] = file_reseller_id
    out["_file_sale_date"] = file_sale_date
    out["_run_id"] = run_id
    return out


def batched_rows(reader: csv.reader, chunk_size: int):
    chunk = []
    row_number = 0
    for raw_row in reader:
        row_number += 1
        chunk.append((row_number, raw_row))
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def process_file(
    settings: Settings,
    engine: Engine,
    run_id: str,
    candidate: FileCandidate,
    schema: dict,
    mode: str,
    logger: logging.Logger,
) -> tuple[int, int, int]:
    """Returns (row_count, rows_inserted, rows_skipped, status) where status
    is one of: SKIPPED_ALREADY_PROCESSED, SKIPPED_CLAIM_LOST, PROCESSED.
    """
    cfg = settings.get("ingestion.reseller")
    columns = schema["columns"]
    tolerance = Decimal(str(cfg["amount_tolerance"]))
    bulk_batch_size = cfg["bulk_batch_size"]
    max_retries = 3
    backoff = 2.0

    stat = candidate.path.stat()
    file_size = stat.st_size
    file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    existing_status = is_file_processed(engine, candidate.file_name)
    if existing_status == "PROCESSED" and not cfg.get("detect_changed_files", False):
        logger.info("Already processed, skipping: %s", candidate.file_name)
        return (0, 0, 0, "SKIPPED_ALREADY_PROCESSED")
    if existing_status == "PROCESSED" and cfg.get("detect_changed_files", False):
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT file_size_bytes, file_modified_at FROM meta.processed_file WHERE file_name = :f"),
                {"f": candidate.file_name},
            ).fetchone()
        if row and row[0] == file_size:
            logger.info("Already processed and unchanged, skipping: %s", candidate.file_name)
            return (0, 0, 0, "SKIPPED_ALREADY_PROCESSED")
        logger.warning("Redelivery detected for %s (size/mtime changed) -- reloading", candidate.file_name)
        existing_status = "FAILED"  # fall through to the reload path below

    if existing_status in ("IN_PROGRESS", "FAILED"):
        logger.warning("Previous attempt at %s left status=%s -- deleting and reloading", candidate.file_name, existing_status)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM raw_reseller.daily_sales WHERE _source_file_name = :f"), {"f": candidate.file_name})

    claimed = claim_file(engine, candidate.file_name, candidate.reseller_id, candidate.sale_date, file_size, file_mtime, run_id)
    if not claimed:
        logger.info("Lost claim race for %s -- another worker/run has it", candidate.file_name)
        return (0, 0, 0, "SKIPPED_CLAIM_LOST")

    encoding = sniff_encoding(candidate.path, cfg["encoding_fallbacks"])
    logger.info("Processing %s (encoding=%s)", candidate.file_name, encoding)

    metadata = MetaData(schema="raw_reseller")
    tbl = Table("daily_sales", metadata, autoload_with=engine)

    row_count = 0
    rows_inserted = 0
    rows_skipped = 0
    seen_ticket_ids: set[str] = set()

    try:
        with open(candidate.path, "r", encoding=encoding, newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                complete_file(engine, candidate.file_name, 0, 0, 0)
                return (0, 0, 0, "PROCESSED")

            for chunk in batched_rows(reader, cfg["csv_chunk_size"]):
                row_count += len(chunk)
                good_rows = []
                for row_number, raw_row in chunk:
                    try:
                        parsed = validate_and_build_row(raw_row, row_number, columns, seen_ticket_ids, schema, tolerance)
                        good_rows.append(
                            _target_row(parsed, columns, candidate.file_name, row_number, candidate.reseller_id, candidate.sale_date, run_id)
                        )
                    except RowValidationError as exc:
                        rows_skipped += 1
                        payload = dict(zip([c["name"] for c in columns], raw_row)) if len(raw_row) == len(columns) else {"raw_row": raw_row}
                        data_error(
                            logger, source=candidate.file_name, row=row_number, reason=exc.reason,
                            detail=exc.detail, payload=payload, run_id=run_id,
                        )

                if good_rows:
                    @retry_on_transient(max_retries=max_retries, backoff_sec=backoff)
                    def _do(rows=good_rows):
                        with engine.begin() as conn:
                            for i in range(0, len(rows), bulk_batch_size):
                                conn.execute(tbl.insert(), rows[i : i + bulk_batch_size])

                    _do()
                    rows_inserted += len(good_rows)
    except Exception:
        fail_file(engine, candidate.file_name)
        raise

    complete_file(engine, candidate.file_name, row_count, rows_inserted, rows_skipped)
    logger.info(
        "Finished %s: row_count=%d rows_inserted=%d rows_skipped=%d",
        candidate.file_name, row_count, rows_inserted, rows_skipped,
    )
    return (row_count, rows_inserted, rows_skipped, "PROCESSED")


def _handle_invalid_name(engine: Engine, candidate: FileCandidate, run_id: str, logger: logging.Logger) -> None:
    already = is_file_processed(engine, candidate.file_name)
    if already == "SKIPPED_INVALID_NAME":
        return  # already recorded on a previous run -- don't re-log
    data_error(
        logger, source=candidate.file_name, row="-", reason="invalid_file_name",
        detail="filename does not match the DailySales_MMDDYYYY_RESELLERID.csv contract or embeds an invalid date",
        payload={}, run_id=run_id,
    )
    mark_skipped_invalid_name(engine, candidate.file_name)


def _worker(settings_path: str, env_path: str, run_id: str, mode: str, candidate: FileCandidate, log_level: str) -> tuple[str, int, int, int, str, Optional[str]]:
    """ProcessPoolExecutor entry point: builds its own engine (SQLAlchemy
    engines can't be forked) and its own logger (log records don't cross
    process boundaries) -- see Appendix C. Returns (file_name, row_count,
    rows_inserted, rows_skipped, status, error_or_None).
    """
    settings = Settings.load(config_path=settings_path, env_path=env_path)
    engine = get_engine(settings)
    proc_tag = f"{run_id}-p{os.getpid()}"
    logger = get_logger("ingest_reseller", run_id=proc_tag, log_dir=settings.path("logs"), level=log_level)
    schema = load_schema(settings)
    try:
        row_count, rows_inserted, rows_skipped, status = process_file(settings, engine, run_id, candidate, schema, mode, logger)
        return (candidate.file_name, row_count, rows_inserted, rows_skipped, status, None)
    except Exception as exc:  # noqa: BLE001
        logger.error("File %s failed: %s", candidate.file_name, exc)
        return (candidate.file_name, 0, 0, 0, "FAILED", str(exc))
    finally:
        engine.dispose()


def run_reseller_ingestion(
    settings: Settings,
    engine: Engine,
    run_id: str,
    mode: str,
    logger: logging.Logger,
    profile_memory: bool = False,
) -> dict:
    """Discovers files, claims/loads the valid ones, and returns a summary
    dict. When profile_memory is True, runs sequentially in this process
    (tracemalloc can't see into ProcessPoolExecutor workers) and prints peak
    memory usage against the configured ceiling.
    """
    schema = load_schema(settings)
    reseller_dir = settings.path("reseller_dir")
    candidates = discover_files(reseller_dir, schema)

    valid = [c for c in candidates if c.valid_name]
    invalid = [c for c in candidates if not c.valid_name]

    for c in invalid:
        _handle_invalid_name(engine, c, run_id, logger)

    summary = {"files_seen": len(candidates), "files_invalid_name": len(invalid), "files_processed": 0, "files_skipped": 0, "files_failed": 0, "rows_read": 0, "rows_inserted": 0, "rows_skipped": 0}

    def _tally(status: str) -> None:
        if status == "PROCESSED":
            summary["files_processed"] += 1
        elif status == "FAILED":
            summary["files_failed"] += 1
        else:  # SKIPPED_ALREADY_PROCESSED / SKIPPED_CLAIM_LOST
            summary["files_skipped"] += 1

    if profile_memory:
        tracemalloc.start()
        for candidate in valid:
            job = JobContext(engine, run_id, f"ingest_reseller_{candidate.file_name}", "reseller", candidate.file_name, "raw_reseller.daily_sales", mode, logger=logger)
            row_count = rows_inserted = rows_skipped = 0
            status = "FAILED"
            with job:
                row_count, rows_inserted, rows_skipped, status = process_file(settings, engine, run_id, candidate, schema, mode, logger)
                job.rows_read, job.rows_inserted, job.rows_skipped = row_count, rows_inserted, rows_skipped
            summary["rows_read"] += row_count
            summary["rows_inserted"] += rows_inserted
            summary["rows_skipped"] += rows_skipped
            _tally(status)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        ceiling_mb = settings.get("ingestion.reseller.memory_ceiling_mb")
        logger.info("Peak memory usage: %.1f MB (ceiling: %d MB)", peak / (1024 * 1024), ceiling_mb)
        summary["peak_memory_mb"] = peak / (1024 * 1024)
    else:
        workers = min(settings.get("ingestion.reseller.workers"), os.cpu_count() or 1)
        settings_path = str(settings.config_path)
        env_path = str(settings.repo_root / ".env")
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_worker, settings_path, env_path, run_id, mode, c, settings.log_level)
                for c in valid
            ]
            for future in as_completed(futures):
                file_name, row_count, rows_inserted, rows_skipped, status, error = future.result()

                job = JobContext(engine, run_id, f"ingest_reseller_{file_name}", "reseller", file_name, "raw_reseller.daily_sales", mode, logger=logger)
                job.rows_read = row_count
                job.rows_inserted = rows_inserted
                job.rows_skipped = rows_skipped
                with job:
                    if error:
                        raise RuntimeError(error)

                summary["rows_read"] += row_count
                summary["rows_inserted"] += rows_inserted
                summary["rows_skipped"] += rows_skipped
                _tally(status)

    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest reseller CSV files into raw_reseller.daily_sales.")
    parser.add_argument("--mode", choices=["full", "incremental"], default="full")
    parser.add_argument("--profile-memory", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    engine = get_engine(settings)
    logger = get_logger("ingest_reseller", run_id="adhoc-reseller", log_dir=settings.path("logs"), level=settings.log_level)

    from src.common.metadata import RunContext

    run = RunContext(engine, log_file="logs/pipeline_adhoc-reseller.log")
    run.start_run(run_type=args.mode.upper(), sources="reseller")
    summary = run_reseller_ingestion(settings, engine, run.run_id, args.mode.upper(), logger, profile_memory=args.profile_memory)
    status = run.finish_run()
    print(f"Run {run.run_id}: {status}")
    print(summary)
    return 0 if status == "SUCCESS" else (1 if status == "PARTIAL" else 2)


if __name__ == "__main__":
    import sys

    sys.exit(main())
