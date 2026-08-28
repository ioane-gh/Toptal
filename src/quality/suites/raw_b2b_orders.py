"""Expectation suite for raw_b2b.orders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import great_expectations.expectations as gxe

from src.common.config import Settings

SUITE_NAME = "raw_b2b_orders"


def build_expectations(settings: Settings) -> list:
    date_range = settings.get("generation.date_range")
    min_ts = datetime.strptime(date_range["start"], "%Y-%m-%d")
    # Upper bound extends past "today" -- mutate_b2b.py / incremental loads
    # insert brand-new orders dated at generation time, not just the static
    # 2019-2020 historical range.
    max_ts = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)

    return [
        gxe.ExpectColumnValuesToNotBeNull(column="order_id", meta={"severity": "CRITICAL"}),
        gxe.ExpectColumnValuesToBeUnique(column="order_id", meta={"severity": "CRITICAL"}),
        gxe.ExpectColumnValuesToBeBetween(column="total_amount", min_value=0, meta={"severity": "CRITICAL"}),
        gxe.ExpectColumnValuesToBeInSet(
            column="order_status", value_set=["COMPLETED", "REFUNDED", "CANCELLED"], meta={"severity": "WARN"}
        ),
        gxe.ExpectColumnValuesToBeInSet(
            column="seller_type", value_set=["ORGANIZER", "RESELLER"], meta={"severity": "WARN"}
        ),
        gxe.ExpectColumnValuesToBeBetween(column="order_ts", min_value=min_ts, max_value=max_ts, meta={"severity": "WARN"}),
    ]
