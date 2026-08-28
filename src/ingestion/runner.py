"""Runner CLI -- the single entry point that drives one pipeline run:
B2B ingestion, reseller ingestion, and (unless --skip-dq) Great
Expectations validation, all recorded under one meta.ingestion_run row.

    python -m src.ingestion.runner \\
      --mode {full|incremental} \\
      --sources b2b,reseller \\
      [--tables orders,order_items] \\
      [--chunk-size N] [--workers N] \\
      [--skip-dq] [--dry-run] [--profile-memory]

Exit code 0 on SUCCESS, 1 on PARTIAL, 2 on FAILED.
"""
from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import text

from src.common.config import Settings, get_settings
from src.common.db import get_engine
from src.common.logging_setup import get_logger
from src.common.metadata import RunContext, get_watermark
from src.common.signals import STOP_EVENT, install_signal_handlers
from src.ingestion.ingest_b2b import TABLE_SPECS as B2B_TABLE_SPECS
from src.ingestion.ingest_b2b import run_b2b_ingestion
from src.ingestion.ingest_reseller import discover_files
from src.ingestion.ingest_reseller import load_schema as load_reseller_schema
from src.ingestion.ingest_reseller import run_reseller_ingestion
from src.quality.run_validations import run_data_quality


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ticketing data platform ingestion runner.")
    parser.add_argument("--mode", choices=["full", "incremental"], required=True)
    parser.add_argument("--sources", default="b2b,reseller", help="comma-separated: b2b,reseller")
    parser.add_argument("--tables", default=None, help="comma-separated B2B table filter (default: all, per load_order)")
    parser.add_argument("--chunk-size", type=int, default=None, help="overrides both b2b chunk_size and reseller csv_chunk_size")
    parser.add_argument("--workers", type=int, default=None, help="overrides both b2b and reseller worker counts")
    parser.add_argument("--skip-dq", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile-memory", action="store_true")
    return parser.parse_args(argv)


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> None:
    if args.chunk_size:
        settings.yaml_cfg["ingestion"]["b2b"]["chunk_size"] = args.chunk_size
        settings.yaml_cfg["ingestion"]["reseller"]["csv_chunk_size"] = args.chunk_size
    if args.workers:
        settings.yaml_cfg["ingestion"]["b2b"]["workers"] = args.workers
        settings.yaml_cfg["ingestion"]["reseller"]["workers"] = args.workers


def _dry_run_report(settings: Settings, engine, sources: list[str], tables_filter: list[str] | None) -> None:
    print("=== DRY RUN (no writes performed) ===")
    if "b2b" in sources:
        load_order = settings.get("ingestion.b2b.load_order")
        tables = [t for t in load_order if not tables_filter or t in tables_filter]
        print(f"\nB2B tables to process ({len(tables)}):")
        for t in tables:
            spec = B2B_TABLE_SPECS[t]
            if spec.has_updated_at:
                wm = get_watermark(engine, "b2b", t)
                print(f"  {t:<28} watermark={wm.isoformat()}")
            else:
                print(f"  {t:<28} full-load only (no updated_at)")

    if "reseller" in sources:
        schema = load_reseller_schema(settings)
        reseller_dir = settings.path("reseller_dir")
        candidates = discover_files(reseller_dir, schema)
        valid = [c for c in candidates if c.valid_name]
        invalid = [c for c in candidates if not c.valid_name]
        total_size = sum(c.path.stat().st_size for c in valid)

        with engine.connect() as conn:
            processed_names = {
                r[0]
                for r in conn.execute(text("SELECT file_name FROM meta.processed_file WHERE status = 'PROCESSED'")).fetchall()
            }
        new_files = [c for c in valid if c.file_name not in processed_names]

        print(f"\nReseller files found: {len(valid)} valid-name, {len(invalid)} invalid-name, ~{total_size:,} bytes total")
        print(f"  Already PROCESSED: {len(valid) - len(new_files)}")
        print(f"  Would be (re)loaded this run: {len(new_files)}")
    print("\n=== END DRY RUN ===")


def _print_summary(engine, run_id: str, status: str, log_file: str) -> None:
    with engine.connect() as conn:
        run_row = conn.execute(
            text("SELECT run_type, started_at, finished_at, rows_inserted, rows_skipped FROM meta.ingestion_run WHERE run_id = :r"),
            {"r": run_id},
        ).fetchone()
        job_rows = conn.execute(
            text(
                """
                SELECT job_name, load_mode, status, duration_sec, rows_read, rows_inserted, rows_updated, rows_skipped, error_message
                FROM meta.ingestion_job WHERE run_id = :r ORDER BY started_at
                """
            ),
            {"r": run_id},
        ).fetchall()
        dq_rows = conn.execute(
            text("SELECT suite_name, success, COUNT(*) AS n FROM meta.data_quality_result WHERE run_id = :r GROUP BY suite_name, success ORDER BY suite_name"),
            {"r": run_id},
        ).fetchall()

    print(f"\n=== Run {run_id} ===")
    if run_row:
        print(f"Status: {status}  type={run_row.run_type}  started={run_row.started_at}  finished={run_row.finished_at}")
        print(f"Totals: rows_inserted={run_row.rows_inserted}  rows_skipped={run_row.rows_skipped}")
    else:
        print(f"Status: {status} (no run row -- nothing was processed)")

    print(f"\nJobs ({len(job_rows)}):")
    for j in job_rows:
        duration = f"{j.duration_sec:.2f}s" if j.duration_sec is not None else "-"
        print(
            f"  [{j.status:7}] {j.job_name:<40} mode={j.load_mode:<11} read={j.rows_read:>8} "
            f"inserted={j.rows_inserted:>8} updated={j.rows_updated:>8} skipped={j.rows_skipped:>6} duration={duration}"
        )
        if j.error_message:
            print(f"      error: {j.error_message[:300]}")

    if dq_rows:
        print(f"\nData quality ({len(dq_rows)} suite/status groups):")
        for r in dq_rows:
            print(f"  {r.suite_name:<40} success={bool(r.success)!s:<6} count={r.n}")

    print(f"\nLog file: {log_file}")


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    _apply_overrides(settings, args)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    tables_filter = [t.strip() for t in args.tables.split(",")] if args.tables else None
    mode = args.mode.upper()

    engine = get_engine(settings)

    if args.dry_run:
        _dry_run_report(settings, engine, sources, tables_filter)
        return 0

    install_signal_handlers()

    run_id_hint = str(uuid.uuid4())
    log_dir = settings.path("logs")
    logger = get_logger("runner", run_id=run_id_hint, log_dir=log_dir, level=settings.log_level)
    log_file = str(log_dir / f"pipeline_{run_id_hint}.log")

    run = RunContext(engine, log_file=log_file)
    run.start_run(run_type=mode, sources=",".join(sources))
    logger.info("Run started: run_id=%s mode=%s sources=%s", run.run_id, mode, sources)

    if "b2b" in sources:
        run_b2b_ingestion(settings, engine, run.run_id, mode, tables_filter, logger)

    if "reseller" in sources and not STOP_EVENT.is_set():
        run_reseller_ingestion(settings, engine, run.run_id, mode, logger, profile_memory=args.profile_memory)

    dq_summary = None
    if not args.skip_dq and settings.get("quality.enabled") and not STOP_EVENT.is_set():
        dq_summary = run_data_quality(settings, engine, run.run_id, logger)

    if STOP_EVENT.is_set():
        logger.error("Run interrupted by SIGINT/SIGTERM")
        run.fail_run("Run interrupted by SIGINT/SIGTERM -- stopped scheduling new work; already in-flight chunks committed normally.")
        status = "FAILED"
    else:
        status = run.finish_run()
        fail_on_critical = settings.get("quality.fail_run_on_critical")
        if dq_summary and dq_summary["critical_failures"] > 0 and fail_on_critical and status != "FAILED":
            run.fail_run(f"{dq_summary['critical_failures']} CRITICAL data quality failures (fail_run_on_critical=true)")
            status = "FAILED"

    _print_summary(engine, run.run_id, status, log_file)

    return 0 if status == "SUCCESS" else (1 if status == "PARTIAL" else 2)


if __name__ == "__main__":
    sys.exit(main())
