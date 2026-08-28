"""DB-independent tests for runner.py's CLI parsing and config overrides."""
from __future__ import annotations

from src.ingestion.runner import _apply_overrides, parse_args


def test_parse_args_defaults():
    args = parse_args(["--mode", "full"])
    assert args.mode == "full"
    assert args.sources == "b2b,reseller"
    assert args.tables is None
    assert not args.skip_dq
    assert not args.dry_run


def test_parse_args_full_options():
    args = parse_args(
        [
            "--mode", "incremental",
            "--sources", "b2b",
            "--tables", "orders,order_items",
            "--chunk-size", "1000",
            "--workers", "2",
            "--skip-dq",
            "--dry-run",
            "--profile-memory",
        ]
    )
    assert args.mode == "incremental"
    assert args.sources == "b2b"
    assert args.tables == "orders,order_items"
    assert args.chunk_size == 1000
    assert args.workers == 2
    assert args.skip_dq
    assert args.dry_run
    assert args.profile_memory


def test_apply_overrides_updates_yaml_cfg(settings):
    args = parse_args(["--mode", "full", "--chunk-size", "777", "--workers", "9"])
    _apply_overrides(settings, args)
    assert settings.get("ingestion.b2b.chunk_size") == 777
    assert settings.get("ingestion.reseller.csv_chunk_size") == 777
    assert settings.get("ingestion.b2b.workers") == 9
    assert settings.get("ingestion.reseller.workers") == 9

    # restore defaults so this session-scoped fixture doesn't leak into other tests
    settings.yaml_cfg["ingestion"]["b2b"]["chunk_size"] = 50000
    settings.yaml_cfg["ingestion"]["reseller"]["csv_chunk_size"] = 100000
    settings.yaml_cfg["ingestion"]["b2b"]["workers"] = 4
    settings.yaml_cfg["ingestion"]["reseller"]["workers"] = 4
