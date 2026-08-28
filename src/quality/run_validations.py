"""Runs Great Expectations checkpoints against raw_b2b and raw_reseller,
writes every expectation result into meta.data_quality_result, and
generates Data Docs. Runs standalone (`make dq`) or as the runner's final
stage (Phase 8), unless --skip-dq / skip_dq=True.
"""
from __future__ import annotations

import logging
import sys

import great_expectations as gx
import great_expectations.expectations as gxe
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.common.config import Settings, get_settings
from src.common.db import get_engine
from src.common.logging_setup import get_logger
from src.common.metadata import RunContext, utcnow
from src.quality.context import (
    build_context,
    get_datasource,
    get_or_create_batch_definition,
    get_or_create_checkpoint,
    get_or_create_query_asset,
    get_or_create_suite,
    get_or_create_table_asset,
    get_or_create_validation_definition,
)
from src.quality.suites import raw_b2b_order_items, raw_b2b_orders, raw_reseller_daily_sales


def _known_reseller_ids(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT reseller_id FROM raw_b2b.resellers")).fetchall()
    return [str(r[0]) for r in rows]


def _build_checkpoint(settings: Settings, engine: Engine, ctx):
    ds = get_datasource(ctx)

    orders_asset = get_or_create_table_asset(ds, "orders_asset", "orders", "raw_b2b")
    orders_bd = get_or_create_batch_definition(orders_asset, "orders_batch")
    orders_suite = gx.ExpectationSuite(name=raw_b2b_orders.SUITE_NAME)
    for exp in raw_b2b_orders.build_expectations(settings):
        orders_suite.add_expectation(exp)
    orders_suite = get_or_create_suite(ctx, orders_suite)
    orders_vd = get_or_create_validation_definition(
        ctx, gx.ValidationDefinition(name=f"{raw_b2b_orders.SUITE_NAME}_vd", data=orders_bd, suite=orders_suite)
    )

    items_asset = get_or_create_table_asset(ds, "order_items_asset", "order_items", "raw_b2b")
    items_bd = get_or_create_batch_definition(items_asset, "order_items_batch")
    items_suite = gx.ExpectationSuite(name=raw_b2b_order_items.SUITE_NAME)
    for exp in raw_b2b_order_items.build_expectations():
        items_suite.add_expectation(exp)
    items_suite = get_or_create_suite(ctx, items_suite)
    items_vd = get_or_create_validation_definition(
        ctx, gx.ValidationDefinition(name=f"{raw_b2b_order_items.SUITE_NAME}_vd", data=items_bd, suite=items_suite)
    )

    orphan_asset = get_or_create_query_asset(ds, "order_items_orphan_asset", raw_b2b_order_items.ORPHAN_QUERY)
    orphan_bd = get_or_create_batch_definition(orphan_asset, "order_items_orphan_batch")
    orphan_suite = gx.ExpectationSuite(name=raw_b2b_order_items.ORPHAN_SUITE_NAME)
    orphan_suite.add_expectation(gxe.ExpectTableRowCountToBeBetween(max_value=0, meta={"severity": "CRITICAL"}))
    orphan_suite = get_or_create_suite(ctx, orphan_suite)
    orphan_vd = get_or_create_validation_definition(
        ctx, gx.ValidationDefinition(name=f"{raw_b2b_order_items.ORPHAN_SUITE_NAME}_vd", data=orphan_bd, suite=orphan_suite)
    )

    sales_asset = get_or_create_table_asset(ds, "daily_sales_asset", "daily_sales", "raw_reseller")
    sales_bd = get_or_create_batch_definition(sales_asset, "daily_sales_batch")
    sales_suite = gx.ExpectationSuite(name=raw_reseller_daily_sales.SUITE_NAME)
    for exp in raw_reseller_daily_sales.build_expectations(_known_reseller_ids(engine)):
        sales_suite.add_expectation(exp)
    sales_suite = get_or_create_suite(ctx, sales_suite)
    sales_vd = get_or_create_validation_definition(
        ctx, gx.ValidationDefinition(name=f"{raw_reseller_daily_sales.SUITE_NAME}_vd", data=sales_bd, suite=sales_suite)
    )

    return get_or_create_checkpoint(
        ctx, gx.Checkpoint(name="raw_layer_checkpoint", validation_definitions=[orders_vd, items_vd, orphan_vd, sales_vd])
    )


def _persist_results(engine: Engine, run_id: str, checkpoint_result, logger: logging.Logger) -> dict:
    rows = []
    critical_failures = 0
    any_failure = False
    per_suite: dict[str, dict] = {}

    for run_result in checkpoint_result.run_results.values():
        suite_name = run_result.suite_name
        per_suite.setdefault(suite_name, {"pass": 0, "fail": 0})
        for r in run_result.results:
            meta = r.expectation_config.meta or {}
            severity = meta.get("severity", "WARN")
            kwargs = r.expectation_config.kwargs
            column_name = kwargs.get("column") or ",".join(kwargs.get("column_list") or [])
            observed = r.result.get("observed_value", r.result)

            rows.append(
                {
                    "run_id": run_id,
                    "suite_name": suite_name,
                    "expectation_type": r.expectation_config.type,
                    "column_name": column_name or None,
                    "success": bool(r.success),
                    "severity": severity,
                    "observed_value": str(observed)[:4000],
                    "unexpected_count": r.result.get("unexpected_count"),
                }
            )

            if r.success:
                per_suite[suite_name]["pass"] += 1
            else:
                per_suite[suite_name]["fail"] += 1
                any_failure = True
                if severity == "CRITICAL":
                    critical_failures += 1
                logger.warning(
                    "DQ FAIL | suite=%s type=%s column=%s severity=%s observed=%s",
                    suite_name, r.expectation_config.type, column_name, severity, str(observed)[:200],
                )

    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO meta.data_quality_result
                        (run_id, suite_name, expectation_type, column_name, success, severity, observed_value, unexpected_count)
                    VALUES
                        (:run_id, :suite_name, :expectation_type, :column_name, :success, :severity, :observed_value, :unexpected_count)
                    """
                ),
                row,
            )

    return {"per_suite": per_suite, "critical_failures": critical_failures, "any_failure": any_failure, "total_expectations": len(rows)}


def run_data_quality(settings: Settings, engine: Engine, run_id: str, logger: logging.Logger) -> dict:
    ctx = build_context(settings)
    checkpoint = _build_checkpoint(settings, engine, ctx)
    result = checkpoint.run()
    summary = _persist_results(engine, run_id, result, logger)
    try:
        ctx.build_data_docs()
    except Exception as exc:  # noqa: BLE001 -- Data Docs generation is best-effort
        logger.warning("Data Docs generation failed: %s", exc)
    logger.info(
        "DQ complete: %d expectations, %d critical failures, per_suite=%s",
        summary["total_expectations"], summary["critical_failures"], summary["per_suite"],
    )
    return summary


def main() -> int:
    settings = get_settings()
    engine = get_engine(settings)
    logger = get_logger("run_validations", run_id="adhoc-dq", log_dir=settings.path("logs"), level=settings.log_level)

    run = RunContext(engine, log_file="logs/pipeline_adhoc-dq.log")
    run.start_run(run_type="FULL", sources="dq")
    summary = run_data_quality(settings, engine, run.run_id, logger)
    status = run.finish_run()

    fail_on_critical = settings.get("quality.fail_run_on_critical")
    if summary["critical_failures"] > 0 and fail_on_critical and status != "FAILED":
        run.fail_run(f"{summary['critical_failures']} CRITICAL data quality failures")
        status = "FAILED"

    print(f"Run {run.run_id}: {status}")
    print(summary["per_suite"])
    return 0 if status == "SUCCESS" else (1 if status == "PARTIAL" else 2)


if __name__ == "__main__":
    sys.exit(main())
