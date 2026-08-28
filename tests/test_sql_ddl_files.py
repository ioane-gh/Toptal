"""DB-independent smoke tests for the sql/*.sql DDL files: batch splitting
and placeholder substitution. These don't need a live SQL Server -- they
catch the class of mistake (unresolved placeholder, empty batch) that would
otherwise only surface when init_db.py actually runs against one.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.common.init_db import SQL_DIR, _split_batches, _substitute

PLAIN_FILES = [
    "01_schemas.sql",
    "02_meta_tables.sql",
    "03_raw_b2b_tables.sql",
    "04_raw_reseller_tables.sql",
]


def test_plain_sql_files_split_into_nonempty_batches():
    for fname in PLAIN_FILES:
        text = (SQL_DIR / fname).read_text(encoding="utf-8")
        batches = _split_batches(text)
        assert batches, f"{fname} produced zero batches"
        for b in batches:
            assert b.strip(), f"{fname} produced an empty batch"


def test_create_database_sql_statements_resolve_placeholders():
    raw = (SQL_DIR / "00_create_database.sql").read_text(encoding="utf-8")
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

    expected = {
        "create_with_paths",
        "create_default_path",
        "resize_default_data_file",
        "resize_default_log_file",
        "set_recovery_simple",
        "set_read_committed_snapshot",
    }
    assert set(blocks.keys()) == expected

    values = {
        "DB_NAME": "ticketing_dwh",
        "DATA_DIR": "",
        "LOG_DIR": "",
        "DATA_FILE_SIZE_MB": "2048",
        "DATA_FILE_GROWTH_MB": "512",
        "LOG_FILE_SIZE_MB": "512",
        "LOG_FILE_GROWTH_MB": "256",
    }
    for name, block in blocks.items():
        resolved = _substitute(block, values)
        assert "$(" not in resolved, f"{name} left an unresolved placeholder: {resolved}"
