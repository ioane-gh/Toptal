"""Expectation suite for raw_b2b.order_items.

The "no orphans against raw_b2b.orders" check is implemented separately in
run_validations.py as its own query asset + suite (ORPHAN_SUITE_NAME) --
order_items LEFT JOIN orders WHERE orders.order_id IS NULL, expected row
count 0. GX's fluent table-asset expectations are single-table, so a
cross-table check needs its own asset, and GX suites are keyed uniquely by
name, so it can't share this module's SUITE_NAME.
"""
from __future__ import annotations

import great_expectations.expectations as gxe

SUITE_NAME = "raw_b2b_order_items"
ORPHAN_SUITE_NAME = "raw_b2b_order_items_orphan_check"

ORPHAN_QUERY = """
    SELECT oi.order_item_id
    FROM raw_b2b.order_items oi
    LEFT JOIN raw_b2b.orders o ON oi.order_id = o.order_id
    WHERE o.order_id IS NULL
"""


def build_expectations() -> list:
    return [
        gxe.ExpectColumnValuesToBeBetween(column="commission_rate", min_value=0, max_value=1, meta={"severity": "CRITICAL"}),
        gxe.ExpectColumnPairValuesAToBeGreaterThanB(
            column_A="gross_amount", column_B="commission_amount", or_equal=True, meta={"severity": "CRITICAL"}
        ),
        gxe.ExpectColumnValuesToBeBetween(column="quantity", min_value=0, strict_min=True, meta={"severity": "CRITICAL"}),
    ]
