"""DB-independent tests for ingest_reseller.py's file-facing logic:
discovery, encoding sniffing, and per-row validation against the real
generated corpus (data/reseller/). Run `make gen-files` first. Doesn't
touch SQL Server.
"""
from __future__ import annotations

import csv
from decimal import Decimal

import pytest

from src.ingestion.ingest_reseller import RowValidationError, batched_rows, discover_files, load_schema, sniff_encoding, validate_and_build_row


@pytest.fixture(scope="module")
def reseller_dir(settings):
    d = settings.path("reseller_dir")
    if not d.exists() or not any(d.glob("*.CSV")):
        pytest.skip("data/reseller has no CSV files -- run `make gen-files` first")
    return d


@pytest.fixture(scope="module")
def schema(settings):
    return load_schema(settings)


def test_malformed_name_file_is_flagged_invalid(reseller_dir, schema):
    candidates = discover_files(reseller_dir, schema)
    invalid = [c for c in candidates if not c.valid_name]
    assert any("DailySales_" in c.file_name and not c.file_name[11:19].isdigit() for c in invalid), (
        "expected the deliberately malformed-name file among the invalid-name candidates"
    )


def test_valid_files_parse_reseller_id_and_date_from_name(reseller_dir, schema):
    candidates = discover_files(reseller_dir, schema)
    valid = [c for c in candidates if c.valid_name]
    assert valid
    for c in valid[:20]:
        assert c.reseller_id is not None
        assert c.sale_date is not None


def test_all_defect_types_are_detected_with_distinct_reasons(settings, reseller_dir, schema):
    tolerance = Decimal(str(settings.get("ingestion.reseller.amount_tolerance")))
    columns = schema["columns"]
    candidates = [c for c in discover_files(reseller_dir, schema) if c.valid_name]

    reasons_seen: set[str] = set()
    for c in candidates:
        encoding = sniff_encoding(c.path, settings.get("ingestion.reseller.encoding_fallbacks"))
        seen_ticket_ids: set[str] = set()
        with open(c.path, "r", encoding=encoding, newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                continue
            for chunk in batched_rows(reader, 200000):
                for row_number, raw_row in chunk:
                    try:
                        validate_and_build_row(raw_row, row_number, columns, seen_ticket_ids, schema, tolerance)
                    except RowValidationError as exc:
                        reasons_seen.add(exc.reason)

    expected = {
        "missing_required",
        "bad_date",
        "negative_quantity",
        "bad_number",
        "amount_mismatch",
        "duplicate_ticket_id",
        "wrong_column_count",
    }
    missing = expected - reasons_seen
    assert not missing, f"defect types never detected in the corpus: {missing}"


def test_sniff_encoding_falls_back_for_latin1_file(settings, reseller_dir):
    fallbacks = settings.get("ingestion.reseller.encoding_fallbacks")
    latin1_files = []
    for path in reseller_dir.glob("*.CSV"):
        try:
            path.read_bytes()[:65536].decode("utf-8")
        except UnicodeDecodeError:
            latin1_files.append(path)
    assert latin1_files, "expected at least one non-UTF-8 file in the generated corpus"
    for path in latin1_files:
        assert sniff_encoding(path, fallbacks) == "latin-1"
