"""Phase 4 acceptance test: a run with one succeeding and one failing job
must end PARTIAL, with rows_inserted/rows_skipped summed correctly from its
jobs. Requires a live SQL Server instance (see tests/conftest.py).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from src.common.metadata import JobContext, RunContext


def test_partial_run_rollup(sql_engine):
    run = RunContext(sql_engine, log_file="logs/pipeline_test.log")
    run.start_run(run_type="FULL", sources="b2b")

    with JobContext(sql_engine, run.run_id, "job_ok", "b2b", "resellers", "raw_b2b.resellers", "FULL") as job:
        job.rows_read = 10
        job.rows_inserted = 10
        job.rows_skipped = 0

    try:
        with JobContext(sql_engine, run.run_id, "job_fail", "b2b", "venues", "raw_b2b.venues", "FULL") as job:
            job.rows_read = 5
            job.rows_inserted = 2
            job.rows_skipped = 3
            raise RuntimeError("simulated transient failure exhausted retries")
    except RuntimeError:
        pytest.fail("JobContext should suppress the exception so the run continues")

    status = run.finish_run()
    assert status == "PARTIAL"

    with sql_engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, rows_inserted, rows_skipped FROM meta.ingestion_run WHERE run_id = :r"),
            {"r": run.run_id},
        ).fetchone()
    assert row.status == "PARTIAL"
    assert row.rows_inserted == 12
    assert row.rows_skipped == 3
