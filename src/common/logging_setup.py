"""Console + rotating-file logging, with a run_id on every record and a
dedicated data_error() helper for greppable, categorized skipped-row logging.
"""
from __future__ import annotations

import contextlib
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(run_id)s | %(job)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class _ContextFilter(logging.Filter):
    """Injects run_id/job defaults so the format string never KeyErrors."""

    def __init__(self, run_id: str, job: str = "-"):
        super().__init__()
        self.run_id = run_id
        self.job = job

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = getattr(record, "run_id", self.run_id)
        record.job = getattr(record, "job", self.job)
        return True


_configured_run_ids: set[str] = set()


def get_logger(name: str, run_id: str, job: str = "-", log_dir: str | Path = "logs", level: str = "INFO") -> logging.Logger:
    """Returns a logger configured with a console handler and a rotating file
    handler at logs/pipeline_{run_id}.log. Safe to call repeatedly for the
    same run_id (handlers are only attached to the root pipeline logger once).
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("pipeline")
    if run_id not in _configured_run_ids:
        root.setLevel(level)
        root.handlers.clear()

        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
        ctx_filter = _ContextFilter(run_id=run_id, job=job)

        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(ctx_filter)
        root.addHandler(console)

        file_handler = RotatingFileHandler(
            log_dir / f"pipeline_{run_id}.log",
            maxBytes=50 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ctx_filter)
        root.addHandler(file_handler)

        _configured_run_ids.add(run_id)

    logger = root.getChild(name) if name != "pipeline" else root
    logger.run_id = run_id  # type: ignore[attr-defined]
    return logger


def _compact_payload(payload: dict[str, Any] | None, max_len: int = 300) -> str:
    if not payload:
        return "{}"
    s = str({k: v for k, v in payload.items()})
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


def data_error(
    logger: logging.Logger,
    *,
    source: str,
    row: Any,
    reason: str,
    detail: str = "",
    payload: dict[str, Any] | None = None,
    run_id: str | None = None,
    job: str | None = None,
) -> None:
    """Logs a skipped row at WARNING in a consistent, greppable shape:
    DATA_ERROR | source=<table|file> | row=<n> | reason=<code> | detail=<...> | payload=<compact row>
    """
    extra = {}
    if run_id:
        extra["run_id"] = run_id
    if job:
        extra["job"] = job
    logger.warning(
        "DATA_ERROR | source=%s | row=%s | reason=%s | detail=%s | payload=%s",
        source,
        row,
        reason,
        detail,
        _compact_payload(payload),
        extra=extra,
    )


@contextlib.contextmanager
def log_duration(logger: logging.Logger, label: str, **extra_fields: Any):
    """Context manager that logs the start/end and elapsed seconds of a block."""
    start = time.monotonic()
    logger.info("START | %s", label)
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        logger.info("END | %s | duration_sec=%.3f", label, elapsed)
