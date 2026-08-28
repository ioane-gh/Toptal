"""Runs sql/*.sql against the target SQL Server instance, in filename order.

sql/00_create_database.sql is special: it runs against `master` under
AUTOCOMMIT (CREATE/ALTER DATABASE cannot run inside a transaction), and only
a subset of its labelled statements apply depending on whether a data/log
directory was configured. Every other file runs against the project
database, split into batches on lines that are exactly "GO" (mirroring
sqlcmd), each executed as one statement -- required because CREATE SCHEMA
must be the only statement in its batch, and because pyodbc has no native
GO batch separator.

Safe to re-run: every DDL statement is guarded with IF NOT EXISTS / IF
OBJECT_ID(...) IS NULL, ALTER DATABASE settings are idempotent, and file
resize statements never shrink (SQL Server no-ops when SIZE == current size).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from src.common.config import Settings, get_settings
from src.common.db import get_engine
from src.common.logging_setup import get_logger

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

_PLACEHOLDER_RE = re.compile(r"\$\(([A-Z_]+)\)")


def _substitute(sql_text: str, values: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"Unresolved placeholder $({key}) -- add it to the substitution map")
        return str(values[key])

    return _PLACEHOLDER_RE.sub(repl, sql_text)


def _run_create_database(settings: Settings, logger: logging.Logger) -> None:
    path = SQL_DIR / "00_create_database.sql"
    raw = path.read_text(encoding="utf-8")

    data_dir = settings.get("database.data_dir", "")
    log_dir = settings.get("database.log_dir", "")
    values = {
        "DB_NAME": settings.mssql_db,
        "DATA_DIR": data_dir,
        "LOG_DIR": log_dir,
        "DATA_FILE_SIZE_MB": str(settings.get("database.data_file_size_mb")),
        "DATA_FILE_GROWTH_MB": str(settings.get("database.data_file_growth_mb")),
        "LOG_FILE_SIZE_MB": str(settings.get("database.log_file_size_mb")),
        "LOG_FILE_GROWTH_MB": str(settings.get("database.log_file_growth_mb")),
    }

    # Parse "-- STATEMENT: <name>" labelled blocks.
    blocks: dict[str, str] = {}
    current_name = None
    current_lines: list[str] = []
    for line in raw.splitlines():
        m = re.match(r"^\s*--\s*STATEMENT:\s*(\S+)", line)
        if m:
            if current_name:
                blocks[current_name] = "\n".join(current_lines).strip()
            current_name = m.group(1)
            current_lines = []
        elif current_name:
            current_lines.append(line)
    if current_name:
        blocks[current_name] = "\n".join(current_lines).strip()

    use_paths = bool(data_dir) and bool(log_dir)
    ordered = (
        ["create_with_paths"]
        if use_paths
        else ["create_default_path", "resize_default_data_file", "resize_default_log_file"]
    )
    ordered += ["set_recovery_simple", "set_read_committed_snapshot"]

    engine = get_engine(settings, database="master").execution_options(isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        from sqlalchemy import text

        for name in ordered:
            stmt = _substitute(blocks[name], values).strip().rstrip(";")
            if not stmt:
                continue
            logger.info("Executing 00_create_database.sql :: %s", name)
            conn.execute(text(stmt))
    engine.dispose()


def _split_batches(sql_text: str) -> list[str]:
    batches: list[list[str]] = [[]]
    for line in sql_text.splitlines():
        if re.match(r"^\s*GO\s*$", line, flags=re.IGNORECASE):
            batches.append([])
        else:
            batches[-1].append(line)
    return ["\n".join(b).strip() for b in batches if "\n".join(b).strip()]


def _run_plain_sql_file(settings: Settings, path: Path, logger: logging.Logger) -> None:
    from sqlalchemy import text

    raw = path.read_text(encoding="utf-8")
    batches = _split_batches(raw)
    engine = get_engine(settings)
    with engine.begin() as conn:
        for i, batch in enumerate(batches):
            logger.info("Executing %s :: batch %d/%d", path.name, i + 1, len(batches))
            conn.execute(text(batch))
    engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sql/*.sql against the target SQL Server instance.")
    parser.parse_args(argv)

    settings = get_settings()
    logger = get_logger("init_db", run_id="init-db", log_dir=settings.path("logs"), level=settings.log_level)

    logger.info("Target: server=%s db=%s driver=%s", settings.mssql_server, settings.mssql_db, settings.odbc_driver)

    _run_create_database(settings, logger)
    logger.info("Database %s ready.", settings.mssql_db)

    for path in sorted(SQL_DIR.glob("*.sql")):
        if path.name == "00_create_database.sql":
            continue
        _run_plain_sql_file(settings, path, logger)

    logger.info("init_db complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
