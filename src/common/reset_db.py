"""`make reset` -- drops and recreates the project database from zero.

Guarded behind --force (or an interactive confirmation) and refuses to run
unless MSSQL_DB matches config.yaml's database.project_db_name, so this can
never be pointed at an unrelated / shared / production database by accident.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from src.common.config import get_settings
from src.common.db import get_engine
from src.common.logging_setup import get_logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drop and recreate the project database.")
    parser.add_argument("--force", action="store_true", help="Skip the interactive confirmation prompt.")
    args = parser.parse_args(argv)

    settings = get_settings()
    logger = get_logger("reset_db", run_id="reset-db", log_dir=settings.path("logs"), level=settings.log_level)

    expected = settings.get("database.project_db_name")
    if settings.mssql_db != expected:
        logger.error(
            "Refusing to reset: MSSQL_DB=%s does not match database.project_db_name=%s in config.yaml.",
            settings.mssql_db,
            expected,
        )
        return 2

    if not args.force:
        answer = input(
            f"This will DROP AND RECREATE database '{settings.mssql_db}' on server "
            f"'{settings.mssql_server}'. Type the database name to confirm: "
        )
        if answer.strip() != settings.mssql_db:
            logger.error("Confirmation did not match. Aborting.")
            return 1

    engine = get_engine(settings, database="master").execution_options(isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        logger.info("Dropping database %s (if it exists)...", settings.mssql_db)
        conn.execute(text(f"IF DB_ID('{settings.mssql_db}') IS NOT NULL "
                           f"ALTER DATABASE [{settings.mssql_db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"))
        conn.execute(text(f"IF DB_ID('{settings.mssql_db}') IS NOT NULL DROP DATABASE [{settings.mssql_db}]"))
    engine.dispose()

    logger.info("Database %s dropped. Run `make init-db` to recreate it.", settings.mssql_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
