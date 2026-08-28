"""Run-control and processing-metadata helpers over the `meta` schema:
RunContext / JobContext for meta.ingestion_run / meta.ingestion_job, plus
watermark and processed-file bookkeeping used by the B2B and reseller
ingestion jobs (Phases 5-6).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

EPOCH_START = datetime(1970, 1, 1, tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunContext:
    """Owns one row in meta.ingestion_run for the lifetime of a pipeline run."""

    def __init__(self, engine: Engine, log_file: str = ""):
        self.engine = engine
        self.log_file = log_file
        self.run_id: Optional[str] = None

    def start_run(self, run_type: str, sources: str) -> str:
        self.run_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO meta.ingestion_run
                        (run_id, run_type, source_filter, started_at, status, rows_inserted, rows_skipped, log_file)
                    VALUES
                        (:run_id, :run_type, :sources, :started_at, 'RUNNING', 0, 0, :log_file)
                    """
                ),
                {
                    "run_id": self.run_id,
                    "run_type": run_type,
                    "sources": sources,
                    "started_at": utcnow(),
                    "log_file": self.log_file,
                },
            )
        return self.run_id

    def finish_run(self) -> str:
        """Rolls up this run's job statuses into SUCCESS/PARTIAL/FAILED and
        records totals + the log file path on the run row.
        """
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT status, rows_inserted, rows_skipped FROM meta.ingestion_job WHERE run_id = :run_id"),
                {"run_id": self.run_id},
            ).fetchall()
            statuses = [r[0] for r in rows]
            rows_inserted = sum(r[1] or 0 for r in rows)
            rows_skipped = sum(r[2] or 0 for r in rows)

            if not statuses or all(s == "SUCCESS" for s in statuses):
                status = "SUCCESS"
            elif all(s == "FAILED" for s in statuses):
                status = "FAILED"
            else:
                status = "PARTIAL"

            conn.execute(
                text(
                    """
                    UPDATE meta.ingestion_run
                    SET finished_at = :finished_at, status = :status,
                        rows_inserted = :rows_inserted, rows_skipped = :rows_skipped,
                        log_file = :log_file
                    WHERE run_id = :run_id
                    """
                ),
                {
                    "finished_at": utcnow(),
                    "status": status,
                    "rows_inserted": rows_inserted,
                    "rows_skipped": rows_skipped,
                    "log_file": self.log_file,
                    "run_id": self.run_id,
                },
            )
        return status

    def fail_run(self, error_message: str) -> None:
        """Used for a hard stop (e.g. SIGINT/SIGTERM) where no further jobs
        will run: marks the run FAILED directly rather than rolling up.
        """
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE meta.ingestion_run
                    SET finished_at = :finished_at, status = 'FAILED', error_message = :error_message, log_file = :log_file
                    WHERE run_id = :run_id
                    """
                ),
                {"finished_at": utcnow(), "error_message": error_message[:4000], "log_file": self.log_file, "run_id": self.run_id},
            )


class JobContext:
    """Context manager for one row in meta.ingestion_job.

    Inserts the job row on enter; on exit, updates counters/status/duration
    and -- critically -- swallows any exception after recording it as a
    FAILED job, so the calling loop naturally continues to the next job
    (the run itself still surfaces the failure via finish_run() -> PARTIAL).
    """

    def __init__(
        self,
        engine: Engine,
        run_id: str,
        job_name: str,
        source_system: str,
        source_object: str,
        target_object: str,
        load_mode: str,
        logger: Optional[logging.Logger] = None,
    ):
        self.engine = engine
        self.run_id = run_id
        self.job_name = job_name
        self.source_system = source_system
        self.source_object = source_object
        self.target_object = target_object
        self.load_mode = load_mode
        self.logger = logger

        self.rows_read = 0
        self.rows_inserted = 0
        self.rows_updated = 0
        self.rows_skipped = 0

        self.job_id: Optional[int] = None
        self._started_at: Optional[datetime] = None

    def __enter__(self) -> "JobContext":
        self._started_at = utcnow()
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO meta.ingestion_job
                        (run_id, job_name, source_system, source_object, target_object, load_mode, status, started_at)
                    OUTPUT inserted.job_id
                    VALUES
                        (:run_id, :job_name, :source_system, :source_object, :target_object, :load_mode, 'RUNNING', :started_at)
                    """
                ),
                {
                    "run_id": self.run_id,
                    "job_name": self.job_name,
                    "source_system": self.source_system,
                    "source_object": self.source_object,
                    "target_object": self.target_object,
                    "load_mode": self.load_mode,
                    "started_at": self._started_at,
                },
            )
            self.job_id = result.scalar()
        if self.logger:
            self.logger.info("Job started: %s", self.job_name, extra={"job": self.job_name})
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        finished_at = utcnow()
        duration = (finished_at - self._started_at).total_seconds()
        status = "FAILED" if exc_type else "SUCCESS"
        error_message = str(exc)[:4000] if exc else None

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE meta.ingestion_job
                    SET status = :status, finished_at = :finished_at, duration_sec = :duration,
                        rows_read = :rows_read, rows_inserted = :rows_inserted,
                        rows_updated = :rows_updated, rows_skipped = :rows_skipped,
                        error_message = :error_message
                    WHERE job_id = :job_id
                    """
                ),
                {
                    "status": status,
                    "finished_at": finished_at,
                    "duration": duration,
                    "rows_read": self.rows_read,
                    "rows_inserted": self.rows_inserted,
                    "rows_updated": self.rows_updated,
                    "rows_skipped": self.rows_skipped,
                    "error_message": error_message,
                    "job_id": self.job_id,
                },
            )

        if exc_type and self.logger:
            self.logger.error("Job failed: %s -- %s", self.job_name, exc, extra={"job": self.job_name})
        elif self.logger:
            self.logger.info(
                "Job finished: %s | rows_read=%d rows_inserted=%d rows_updated=%d rows_skipped=%d duration_sec=%.2f",
                self.job_name,
                self.rows_read,
                self.rows_inserted,
                self.rows_updated,
                self.rows_skipped,
                duration,
                extra={"job": self.job_name},
            )
        return exc_type is not None  # suppress -- run continues to the next job


# --------------------------------------------------------------------------
# Watermarks
# --------------------------------------------------------------------------

def get_watermark(engine: Engine, source_system: str, source_object: str) -> datetime:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT watermark_value FROM meta.watermark WHERE source_system = :s AND source_object = :o"),
            {"s": source_system, "o": source_object},
        ).fetchone()
    if row is None:
        return EPOCH_START
    value = row[0]
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def set_watermark(
    engine: Engine,
    source_system: str,
    source_object: str,
    watermark_column: str,
    watermark_value: datetime,
    run_id: Optional[str],
) -> None:
    """Only call this after every chunk of the job has committed."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                MERGE meta.watermark AS tgt
                USING (SELECT :s AS source_system, :o AS source_object) AS src
                    ON tgt.source_system = src.source_system AND tgt.source_object = src.source_object
                WHEN MATCHED THEN UPDATE SET
                    watermark_column = :col, watermark_value = :val, last_run_id = :run_id, updated_at = :now
                WHEN NOT MATCHED THEN INSERT (source_system, source_object, watermark_column, watermark_value, last_run_id, updated_at)
                    VALUES (:s, :o, :col, :val, :run_id, :now);
                """
            ),
            {
                "s": source_system,
                "o": source_object,
                "col": watermark_column,
                "val": watermark_value,
                "run_id": run_id,
                "now": utcnow(),
            },
        )


# --------------------------------------------------------------------------
# Processed files (reseller CSV incrementality)
# --------------------------------------------------------------------------

def is_file_processed(engine: Engine, file_name: str) -> Optional[str]:
    """Returns the file's current status, or None if it has never been seen."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM meta.processed_file WHERE file_name = :f"), {"f": file_name}
        ).fetchone()
    return row[0] if row else None


def claim_file(
    engine: Engine,
    file_name: str,
    reseller_id: Optional[str],
    sale_date,
    file_size_bytes: int,
    file_modified_at: datetime,
    run_id: str,
) -> bool:
    """Atomically flips the file to IN_PROGRESS. Returns True if this call
    claimed it, False if another worker already holds it. Races are handled
    by the file_name UNIQUE constraint (a losing concurrent INSERT raises
    IntegrityError) and by only UPDATE-ing rows not already IN_PROGRESS.
    """
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE meta.processed_file
                SET status = 'IN_PROGRESS', run_id = :run_id, processed_at = NULL,
                    file_size_bytes = :size, file_modified_at = :mtime
                WHERE file_name = :f AND status <> 'IN_PROGRESS'
                """
            ),
            {"run_id": run_id, "size": file_size_bytes, "mtime": file_modified_at, "f": file_name},
        )
        if result.rowcount > 0:
            return True

        exists = conn.execute(text("SELECT 1 FROM meta.processed_file WHERE file_name = :f"), {"f": file_name}).fetchone()
        if exists:
            return False  # already IN_PROGRESS -- claimed elsewhere

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO meta.processed_file
                        (file_name, reseller_id, sale_date, file_size_bytes, file_modified_at, status, run_id)
                    VALUES
                        (:f, :rid, :sd, :size, :mtime, 'IN_PROGRESS', :run_id)
                    """
                ),
                {"f": file_name, "rid": reseller_id, "sd": sale_date, "size": file_size_bytes, "mtime": file_modified_at, "run_id": run_id},
            )
        return True
    except IntegrityError:
        return False  # lost the race to a concurrent claim


def complete_file(engine: Engine, file_name: str, row_count: int, rows_inserted: int, rows_skipped: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE meta.processed_file
                SET status = 'PROCESSED', row_count = :rc, rows_inserted = :ri, rows_skipped = :rs, processed_at = :now
                WHERE file_name = :f
                """
            ),
            {"rc": row_count, "ri": rows_inserted, "rs": rows_skipped, "now": utcnow(), "f": file_name},
        )


def fail_file(engine: Engine, file_name: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE meta.processed_file SET status = 'FAILED', processed_at = :now WHERE file_name = :f"),
            {"now": utcnow(), "f": file_name},
        )


def mark_skipped_invalid_name(engine: Engine, file_name: str) -> None:
    """Records a malformed file name once, so subsequent runs don't re-log it."""
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM meta.processed_file WHERE file_name = :f"), {"f": file_name}).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                INSERT INTO meta.processed_file (file_name, status, processed_at)
                VALUES (:f, 'SKIPPED_INVALID_NAME', :now)
                """
            ),
            {"f": file_name, "now": utcnow()},
        )
