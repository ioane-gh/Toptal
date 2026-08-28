"""Phase 7 acceptance test: runs the DQ checkpoints end-to-end and checks
that all four suites (three from the spec plus the orphan-check suite --
see NOTES.md) produced results for the run. Requires a live SQL Server
instance with raw_b2b/raw_reseller already populated (see tests/conftest.py).
"""
from __future__ import annotations

from sqlalchemy import text

from src.common.metadata import RunContext
from src.quality.run_validations import run_data_quality


def test_dq_writes_results_for_all_suites(settings, sql_engine, tmp_path):
    run = RunContext(sql_engine, log_file=str(tmp_path / "dq.log"))
    run.start_run(run_type="FULL", sources="dq")

    logger = __import__("logging").getLogger("test_dq")
    summary = run_data_quality(settings, sql_engine, run.run_id, logger)
    run.finish_run()

    expected_suites = {
        "raw_b2b_orders",
        "raw_b2b_order_items",
        "raw_b2b_order_items_orphan_check",
        "raw_reseller_daily_sales",
    }
    assert expected_suites <= set(summary["per_suite"].keys())

    with sql_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT suite_name, success, COUNT(*) AS n FROM meta.data_quality_result WHERE run_id = :r GROUP BY suite_name, success"),
            {"r": run.run_id},
        ).fetchall()
    suites_with_results = {r.suite_name for r in rows}
    assert expected_suites <= suites_with_results
