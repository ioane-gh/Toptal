"""DB-independent tests for ingest_b2b.py's SQLite-facing logic: row
conversion and keyset pagination against the real generated source
(data/source/b2b.db). Run `make gen-b2b` first. Doesn't touch SQL Server.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.ingestion.ingest_b2b import TABLE_SPECS, _build_merge_sql, convert_row, read_chunks_full, read_chunks_incremental, sqlite_connect


@pytest.fixture(scope="module")
def sqlite_conn(settings):
    db_path = settings.path("sqlite_db")
    if not db_path.exists():
        pytest.skip("data/source/b2b.db missing -- run `make gen-b2b` first")
    conn = sqlite_connect(db_path)
    yield conn
    conn.close()


@pytest.mark.parametrize("table", list(TABLE_SPECS.keys()))
def test_every_row_converts_cleanly(sqlite_conn, table):
    spec = TABLE_SPECS[table]
    total = 0
    for chunk in read_chunks_full(sqlite_conn, table, spec, 5000):
        for row in chunk:
            convert_row(spec, row)  # raises on failure
        total += len(chunk)
    assert total > 0


def test_full_pagination_matches_count(sqlite_conn):
    spec = TABLE_SPECS["orders"]
    n = sum(len(c) for c in read_chunks_full(sqlite_conn, "orders", spec, 777))
    expected = sqlite_conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert n == expected


def test_incremental_window_from_epoch_returns_everything(sqlite_conn):
    spec = TABLE_SPECS["orders"]
    wm = datetime(1970, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hb = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = sum(len(c) for c in read_chunks_incremental(sqlite_conn, "orders", spec, wm, hb, 5000))
    expected = sqlite_conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert n == expected


def test_merge_sql_builds_for_all_incremental_tables():
    for table, spec in TABLE_SPECS.items():
        if not spec.has_updated_at:
            continue
        sql = _build_merge_sql(table, spec, f"stg_{table}")
        assert "MERGE raw_b2b." in sql
        assert "OUTPUT $action" in sql
        assert spec.pk in sql
