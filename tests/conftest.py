"""Shared pytest fixtures. Tests that need a live SQL Server instance are
skipped automatically when one isn't reachable -- this sandbox has neither a
SQL Server instance nor the Microsoft ODBC driver installed (see NOTES.md),
but the fixture works unmodified against a real target.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import Settings
from src.common.db import get_engine


def _sql_server_reachable(settings: Settings) -> bool:
    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.load()


@pytest.fixture(scope="session")
def sql_engine(settings: Settings):
    if not _sql_server_reachable(settings):
        pytest.skip("No reachable SQL Server instance / ODBC driver in this environment -- see NOTES.md")
    return get_engine(settings)
