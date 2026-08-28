"""Simulates operational churn on data/source/b2b.db so incremental ingestion
has real work to do: updates ~2% of orders / order_items, and inserts a
batch of brand-new orders dated today.

Touched rows get a fresh updated_at (UTC now); created_at is never modified.
Prints inserted vs. updated counts per table. Safe to run repeatedly -- each
run is a fresh independent churn pass, exactly like real operational traffic.
"""
from __future__ import annotations

import random
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from src.common.config import Settings, get_settings
from src.generators.generate_b2b import ITEM_COUNT_PROBS, ITEM_COUNT_WEIGHTS, d2, d6, iso, weighted_choice


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_lookups(conn: sqlite3.Connection):
    event_type_of: dict[int, str] = {}
    event_currency: dict[int, str] = {}
    all_event_ids: list[int] = []
    for eid, etype in conn.execute("SELECT event_id, event_type FROM events"):
        event_type_of[eid] = etype
        all_event_ids.append(eid)

    tt_by_event: dict[int, list[int]] = {}
    tt_face_value: dict[int, Decimal] = {}
    for ttid, eid, face, currency in conn.execute("SELECT ticket_type_id, event_id, face_value, currency FROM ticket_types"):
        tt_by_event.setdefault(eid, []).append(ttid)
        tt_face_value[ttid] = Decimal(face)
        event_currency.setdefault(eid, currency)

    platform_reseller_ids: list[int] = []
    agreements_index: dict[int, list[tuple[str, str | None, Decimal]]] = {}
    reseller_integration = dict(conn.execute("SELECT reseller_id, integration_type FROM resellers").fetchall())
    for rid, rate, vfrom, vto in conn.execute("SELECT reseller_id, commission_rate, valid_from, valid_to FROM partnership_agreements"):
        agreements_index.setdefault(rid, []).append((vfrom, vto, Decimal(rate)))
    for rid, itype in reseller_integration.items():
        if itype == "PLATFORM":
            platform_reseller_ids.append(rid)

    max_order_id = conn.execute("SELECT MAX(order_id) FROM orders").fetchone()[0]
    max_item_id = conn.execute("SELECT MAX(order_item_id) FROM order_items").fetchone()[0]

    return dict(
        event_type_of=event_type_of,
        event_currency=event_currency,
        all_event_ids=all_event_ids,
        tt_by_event=tt_by_event,
        tt_face_value=tt_face_value,
        platform_reseller_ids=platform_reseller_ids,
        agreements_index=agreements_index,
        max_order_id=max_order_id,
        max_item_id=max_item_id,
    )


def commission_rate_for(agreements_index, rid: int, at_iso_date: str) -> Decimal:
    for vf, vt, rate in agreements_index.get(rid, []):
        if vf <= at_iso_date and (vt is None or at_iso_date <= vt):
            return rate
    return Decimal("0")


def mutate(settings: Settings) -> None:
    rng = random.Random(settings.seed + 1)  # offset so mutation draws differ from generation draws

    db_path = settings.path("sqlite_db")
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} does not exist -- run `make gen-b2b` first")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")

    mutation_cfg = settings.get("generation.mutation")
    ratio = mutation_cfg["updated_row_ratio"]
    new_orders_n = mutation_cfg["new_orders"]
    now = now_iso()

    updated = {"orders": 0, "order_items": 0}
    inserted = {"orders": 0, "order_items": 0}

    # ---- update ~ratio of orders: flip a subset to REFUNDED ----
    order_ids = [r[0] for r in conn.execute("SELECT order_id FROM orders")]
    n_update_orders = max(1, round(len(order_ids) * ratio))
    for oid in rng.sample(order_ids, n_update_orders):
        conn.execute(
            "UPDATE orders SET order_status='REFUNDED', updated_at=? WHERE order_id=?",
            (now, oid),
        )
        updated["orders"] += 1

    # ---- update ~ratio of order_items: quantity corrections ----
    item_rows = conn.execute("SELECT order_item_id, unit_price, commission_rate FROM order_items").fetchall()
    n_update_items = max(1, round(len(item_rows) * ratio))
    for item_id, unit_price, commission_rate in rng.sample(item_rows, n_update_items):
        new_qty = rng.randint(1, 6)
        unit_price = Decimal(unit_price)
        commission_rate = Decimal(commission_rate)
        gross = (unit_price * new_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        commission_amount = (gross * commission_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        conn.execute(
            "UPDATE order_items SET quantity=?, gross_amount=?, commission_amount=?, updated_at=? WHERE order_item_id=?",
            (new_qty, d2(gross), d2(commission_amount), now, item_id),
        )
        updated["order_items"] += 1

    conn.commit()

    # ---- insert new_orders_n brand-new orders dated today ----
    lk = load_lookups(conn)
    order_id = lk["max_order_id"] + 1
    item_id = lk["max_item_id"] + 1
    today = datetime.now(timezone.utc)
    today_iso_date = today.strftime("%Y-%m-%d")

    order_rows = []
    item_rows_new = []
    for _ in range(new_orders_n):
        if lk["platform_reseller_ids"] and rng.random() < 0.30:
            seller_type, rid = "RESELLER", rng.choice(lk["platform_reseller_ids"])
            channel_id = weighted_choice(rng, list(range(1, 6)), [0.05, 0.1, 0.1, 0.7, 0.05])
        else:
            seller_type, rid = "ORGANIZER", None
            channel_id = weighted_choice(rng, list(range(1, 6)), [0.25, 0.35, 0.25, 0.02, 0.13])

        eid = rng.choice(lk["all_event_ids"])
        commission_rate = commission_rate_for(lk["agreements_index"], rid, today_iso_date) if rid else Decimal("0")
        currency = lk["event_currency"].get(eid, "USD")
        n_items = weighted_choice(rng, ITEM_COUNT_WEIGHTS, ITEM_COUNT_PROBS)

        total_amount = Decimal("0")
        total_qty = 0
        order_dt = today.replace(hour=rng.randint(0, 23), minute=rng.randint(0, 59), second=rng.randint(0, 59))
        created = iso(order_dt)
        tt_options = lk["tt_by_event"].get(eid) or []
        for _ in range(n_items):
            if not tt_options:
                continue
            ttid = rng.choice(tt_options)
            qty = rng.randint(1, 6)
            unit_price = lk["tt_face_value"][ttid]
            gross = (unit_price * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            commission_amount = (gross * commission_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            item_rows_new.append(
                (item_id, order_id, eid, ttid, qty, d2(unit_price), d2(gross), d6(commission_rate), d2(commission_amount), created, created)
            )
            total_amount += gross
            total_qty += qty
            item_id += 1

        order_rows.append(
            (
                order_id,
                seller_type,
                rid,
                channel_id,
                created,
                currency,
                d2(total_amount),
                total_qty,
                "COMPLETED",
                created,
                created,
            )
        )
        order_id += 1

    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)", order_rows)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?,?,?,?,?,?)", item_rows_new)
    inserted["orders"] = len(order_rows)
    inserted["order_items"] = len(item_rows_new)
    conn.commit()
    conn.close()

    print(f"Mutated data/source/b2b.db at {now}")
    print("  Updated:")
    for table, n in updated.items():
        print(f"    {table:<20} {n:>8,}")
    print("  Inserted:")
    for table, n in inserted.items():
        print(f"    {table:<20} {n:>8,}")


def main() -> int:
    settings = get_settings()
    mutate(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
