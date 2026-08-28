"""SQL Server engine construction, retry, and bulk-insert helpers.

The ODBC connection string is built by hand and passed to SQLAlchemy through
odbc_connect=... rather than assembled into a mssql+pyodbc://user:pass@host/db
URL. The driver name contains braces and spaces, and passwords can contain
characters that break URL parsing -- odbc_connect sidesteps all of that.
"""
from __future__ import annotations

import functools
import logging
import time
import urllib.parse
from typing import Any, Callable, Iterable, TypeVar

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

logger = logging.getLogger("pipeline.db")

T = TypeVar("T")

# Transient SQL Server error numbers worth retrying (deadlock, timeout,
# connection reset, "server not currently available", etc).
TRANSIENT_SQLSTATE_PREFIXES = ("08", "40001", "HYT00")
TRANSIENT_KEYWORDS = (
    "deadlock",
    "timeout expired",
    "transport-level error",
    "connection is busy",
    "connection reset",
    "server is not currently available",
)


def build_odbc_string(s, database: str | None = None) -> str:
    parts = [
        f"DRIVER={{{s.odbc_driver}}}",
        f"SERVER={s.mssql_server}",
        f"DATABASE={database or s.mssql_db}",
    ]
    if s.mssql_trusted_connection:
        parts.append("Trusted_Connection=yes")
    else:
        parts += [f"UID={s.mssql_user}", f"PWD={s.mssql_password}"]

    # Driver 18 flips the Encrypt default to yes and will refuse a local
    # instance's self-signed certificate unless we opt out explicitly.
    # Driver 17 defaults to Encrypt=no, so this only kicks in for 18+.
    if "18" in s.odbc_driver:
        parts.append("TrustServerCertificate=yes")

    return ";".join(parts) + ";"


def get_engine(s, database: str | None = None, **engine_kwargs: Any) -> Engine:
    logger.info("Using ODBC driver: %s (database=%s)", s.odbc_driver, database or s.mssql_db)
    odbc_str = build_odbc_string(s, database)
    kwargs = {"fast_executemany": True, **engine_kwargs}
    return create_engine(
        "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc_str),
        **kwargs,
    )


def is_transient_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in TRANSIENT_KEYWORDS)


def retry_on_transient(max_retries: int = 3, backoff_sec: float = 2.0) -> Callable:
    """Decorator: retries the wrapped call on transient DB errors with
    exponential backoff. Re-raises the last exception once retries are spent,
    and re-raises immediately on a non-transient error.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempt = 0
            delay = backoff_sec
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - deliberately broad, re-raised below
                    attempt += 1
                    if not is_transient_error(exc) or attempt > max_retries:
                        raise
                    logger.warning(
                        "Transient error on attempt %d/%d for %s: %s -- retrying in %.1fs",
                        attempt,
                        max_retries,
                        func.__name__,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= 2

        return wrapper

    return decorator


def bulk_insert(engine: Engine, rows: Iterable[dict], table: str, schema: str, batch_size: int = 10000) -> int:
    """Inserts rows (list of dicts, all with the same keys) into schema.table
    in batches using fast_executemany. Returns the number of rows inserted.
    """
    from sqlalchemy import MetaData, Table

    rows = list(rows)
    if not rows:
        return 0

    metadata = MetaData(schema=schema)
    tbl = Table(table, metadata, autoload_with=engine)

    inserted = 0
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            conn.execute(tbl.insert(), batch)
            inserted += len(batch)
    return inserted
