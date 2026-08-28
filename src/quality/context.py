"""A file-backed Great Expectations context (under gx/) wired to the same
SQL Server target as the rest of the pipeline, using the installed GX 1.x
fluent API (data sources / assets / batch definitions / suites / validation
definitions / checkpoints) -- confirmed against the actually-installed
version (great_expectations==1.21.x here; 0.18 and 1.x differ significantly,
see NOTES.md).

Every get_or_create_* helper is idempotent: `make dq` is safe to re-run,
and GX's fluent API raises on a duplicate add rather than upserting, so each
helper checks first and only creates on a LookupError.
"""
from __future__ import annotations

import urllib.parse

import great_expectations as gx

from src.common.config import Settings
from src.common.db import build_odbc_string

DATASOURCE_NAME = "mssql_raw"


def get_connection_string(settings: Settings) -> str:
    odbc_str = build_odbc_string(settings)
    return "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc_str)


def build_context(settings: Settings):
    gx_root = settings.repo_root / "gx"
    gx_root.mkdir(parents=True, exist_ok=True)
    ctx = gx.get_context(mode="file", project_root_dir=str(gx_root))
    ctx.data_sources.add_or_update_sql(name=DATASOURCE_NAME, connection_string=get_connection_string(settings))
    return ctx


def get_datasource(ctx):
    return ctx.data_sources.get(DATASOURCE_NAME)


def get_or_create_table_asset(datasource, name: str, table_name: str, schema_name: str):
    try:
        return datasource.get_asset(name)
    except LookupError:
        return datasource.add_table_asset(name=name, table_name=table_name, schema_name=schema_name)


def get_or_create_query_asset(datasource, name: str, query: str):
    try:
        return datasource.get_asset(name)
    except LookupError:
        return datasource.add_query_asset(name=name, query=query)


def get_or_create_batch_definition(asset, name: str):
    try:
        return asset.get_batch_definition(name)
    except LookupError:
        return asset.add_batch_definition_whole_table(name=name)


def get_or_create_suite(ctx, suite):
    return ctx.suites.add_or_update(suite)


def get_or_create_validation_definition(ctx, validation_definition):
    return ctx.validation_definitions.add_or_update(validation_definition)


def get_or_create_checkpoint(ctx, checkpoint):
    return ctx.checkpoints.add_or_update(checkpoint)
