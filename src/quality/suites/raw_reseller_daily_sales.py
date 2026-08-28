"""Expectation suite for raw_reseller.daily_sales."""
from __future__ import annotations

import great_expectations.expectations as gxe

SUITE_NAME = "raw_reseller_daily_sales"


def build_expectations(known_reseller_ids: list[str]) -> list:
    return [
        gxe.ExpectColumnValuesToNotBeNull(column="_source_file_name", meta={"severity": "CRITICAL"}),
        gxe.ExpectColumnValuesToBeInSet(
            column="_file_reseller_id", value_set=known_reseller_ids, meta={"severity": "WARN"}
        ),
        gxe.ExpectCompoundColumnsToBeUnique(
            column_list=["_source_file_name", "_source_row_number"], meta={"severity": "CRITICAL"}
        ),
    ]
