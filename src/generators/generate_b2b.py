"""Generates the source B2B operational database at data/source/b2b.db (SQLite).

organizers and customers were removed (see NOTES.md "Removing organizers
and customers") -- neither is read by any of the four report requirements.
One consequence: an order's items now all come from a single event (they
used to be constrained to "any event under the same organizer," which no
longer exists as a grouping) -- this also incidentally removes a
currency-consistency risk, since currency is assigned per event.

Deterministic from DATA_SEED / run.seed. Safe to re-run: the SQLite file is
recreated from scratch each time, so two runs produce an identical
COUNT(*) / SUM(total_amount) fingerprint. Fast: executemany in batches,
WAL + synchronous=OFF during generation, indexes created after the bulk load.
"""
from __future__ import annotations

import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from faker import Faker

from src.common.config import Settings, get_settings
from src.generators.b2b_schema import (
    CHANNELS,
    CREATE_TABLES_SQL,
    CURRENCIES,
    EVENT_TYPES,
    INDEXES_SQL,
    TABLE_ORDER,
)

BATCH_SIZE = 20000
RESELLER_ORDER_RATIO = 0.30  # share of orders placed through a (platform) reseller
ITEM_COUNT_WEIGHTS = [1, 2, 3, 4]
ITEM_COUNT_PROBS = [0.45, 0.30, 0.17, 0.08]
ORDER_STATUS_CHOICES = ["COMPLETED", "REFUNDED", "CANCELLED"]
ORDER_STATUS_PROBS = [0.85, 0.10, 0.05]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def d2(value) -> str:
    """Format a Decimal to 2dp as text (SQLite has no DECIMAL type)."""
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def d6(value) -> str:
    return str(Decimal(value).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


@dataclass
class GenContext:
    rng: random.Random
    fake: Faker
    settings: Settings
    date_start: date
    date_end: date


def onboarding_datetime(ctx: GenContext) -> datetime:
    """A random 'account created' timestamp before the sales date range starts."""
    window_start = ctx.date_start - timedelta(days=730)
    window_end = ctx.date_start - timedelta(days=30)
    days = (window_end - window_start).days
    d = window_start + timedelta(days=ctx.rng.randint(0, max(days, 1)))
    return datetime(d.year, d.month, d.day, ctx.rng.randint(0, 23), ctx.rng.randint(0, 59), tzinfo=timezone.utc)


def month_weights(ctx: GenContext) -> list[tuple[date, date, float]]:
    """Monthly (start, end, weight) buckets across the date range, with a
    deliberate Feb-2020 spike (see NOTES.md) and mild summer/holiday seasonality.
    """
    buckets = []
    y, m = ctx.date_start.year, ctx.date_start.month
    while (y, m) <= (ctx.date_end.year, ctx.date_end.month):
        start = date(y, m, 1)
        end = date(y + 1, 1, 1) - timedelta(days=1) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)
        start = max(start, ctx.date_start)
        end = min(end, ctx.date_end)

        weight = 1.0
        if m in (6, 7, 8):
            weight *= 1.3  # festival season
        if m == 12:
            weight *= 1.2  # holiday concerts
        if (y, m) == (2020, 2):
            weight *= 1.6  # deliberate YoY spike -- see NOTES.md R2
        buckets.append((start, end, weight))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return buckets


def weighted_choice(rng: random.Random, items: list, weights: list[float]):
    return rng.choices(items, weights=weights, k=1)[0]


def sample_order_datetime(ctx: GenContext, buckets: list[tuple[date, date, float]]) -> datetime:
    start, end, _ = weighted_choice(ctx.rng, buckets, [b[2] for b in buckets])
    days = (end - start).days
    d = start + timedelta(days=ctx.rng.randint(0, max(days, 0)))
    return datetime(d.year, d.month, d.day, ctx.rng.randint(0, 23), ctx.rng.randint(0, 59), ctx.rng.randint(0, 59), tzinfo=timezone.utc)


def generate(settings: Settings) -> None:
    seed = settings.seed
    rng = random.Random(seed)
    fake = Faker()
    Faker.seed(seed)

    date_range = settings.get("generation.date_range")
    date_start = datetime.strptime(date_range["start"], "%Y-%m-%d").date()
    date_end = datetime.strptime(date_range["end"], "%Y-%m-%d").date()
    ctx = GenContext(rng=rng, fake=fake, settings=settings, date_start=date_start, date_end=date_end)

    vol = settings.profile_volumes

    db_path = settings.path("sqlite_db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(CREATE_TABLES_SQL)

    counts: dict[str, int] = {}

    # ---- venues ----
    venues = []
    venue_region: dict[int, str] = {}
    for vid in range(1, vol["venues"] + 1):
        created = onboarding_datetime(ctx)
        region = fake.state() if hasattr(fake, "state") else fake.city()
        venue_region[vid] = region
        venues.append(
            (
                vid,
                fake.company() + " Arena",
                fake.city(),
                region,
                fake.country_code(),
                rng.randint(200, 60000),
                iso(created),
                iso(created),
            )
        )
    conn.executemany("INSERT INTO venues VALUES (?,?,?,?,?,?,?,?)", venues)
    counts["venues"] = len(venues)

    # ---- events ----
    buckets = month_weights(ctx)
    events = []
    event_type_of: dict[int, str] = {}
    event_currency: dict[int, str] = {}
    all_event_ids: list[int] = []
    for eid in range(1, vol["events"] + 1):
        vid = rng.randint(1, vol["venues"])
        etype = rng.choice(EVENT_TYPES)
        start, end, _ = weighted_choice(rng, buckets, [b[2] for b in buckets])
        days = (end - start).days
        edate = start + timedelta(days=rng.randint(0, max(days, 0)))
        created = onboarding_datetime(ctx)
        status = rng.choices(["SCHEDULED", "COMPLETED", "CANCELLED"], weights=[0.3, 0.65, 0.05], k=1)[0]
        events.append((eid, vid, f"{fake.catch_phrase()} {etype.title()}", etype, iso_date(edate), status, iso(created), iso(created)))
        event_type_of[eid] = etype
        event_currency[eid] = rng.choice(CURRENCIES)
        all_event_ids.append(eid)
    conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", events)
    counts["events"] = len(events)

    # ---- ticket_types ----
    ticket_types = []
    tt_by_event: dict[int, list[int]] = {}
    tt_face_value: dict[int, Decimal] = {}
    tt_id = 1
    TT_NAMES = ["General Admission", "VIP", "Early Bird", "Premium", "Standing"]
    for eid in range(1, vol["events"] + 1):
        currency = event_currency[eid]
        n_types = rng.randint(1, min(4, len(TT_NAMES)))
        chosen_names = rng.sample(TT_NAMES, n_types)
        created = onboarding_datetime(ctx)
        for name in chosen_names:
            face = Decimal(rng.randint(1500, 25000)) / 100
            ticket_types.append((tt_id, eid, name, d2(face), currency, iso(created), iso(created)))
            tt_by_event.setdefault(eid, []).append(tt_id)
            tt_face_value[tt_id] = face
            tt_id += 1
    conn.executemany("INSERT INTO ticket_types VALUES (?,?,?,?,?,?,?)", ticket_types)
    counts["ticket_types"] = len(ticket_types)

    # ---- resellers ----
    resellers = []
    reseller_integration: dict[int, str] = {}
    third_party_ratio = settings.get("generation.third_party_reseller_ratio")
    n_third_party = round(vol["resellers"] * third_party_ratio)
    for rid in range(1, vol["resellers"] + 1):
        itype = "THIRD_PARTY" if rid <= n_third_party else "PLATFORM"
        created = onboarding_datetime(ctx)
        reseller_integration[rid] = itype
        resellers.append(
            (
                rid,
                fake.company() + " Tickets",
                fake.country_code(),
                fake.state() if hasattr(fake, "state") else fake.city(),
                fake.city(),
                itype,
                1 if rng.random() > 0.05 else 0,
                iso(created),
                iso(created),
            )
        )
    conn.executemany("INSERT INTO resellers VALUES (?,?,?,?,?,?,?,?,?)", resellers)
    counts["resellers"] = len(resellers)

    # ---- partnership_agreements ----
    # Every reseller (platform and third-party alike) gets its own commission
    # rate history, covering the whole date range with no gaps so a sale at
    # any point in [date_start, date_end] resolves to a rate.
    agreements = []
    agreement_id = 1
    # reseller_id -> sorted list of (valid_from, valid_to_or_None, rate)
    agreements_index: dict[int, list[tuple[date, date | None, Decimal]]] = {}
    platform_reseller_ids: list[int] = []
    for rid in range(1, vol["resellers"] + 1):
        n_rate_changes = rng.choices([1, 2], weights=[0.7, 0.3], k=1)[0]
        valid_from = date_start - timedelta(days=180)
        for i in range(n_rate_changes):
            rate = Decimal(rng.randint(5, 30)) / 100
            is_last = i == n_rate_changes - 1
            if is_last:
                valid_to = None
            else:
                span_days = (date_end - date_start).days // 2
                valid_to = valid_from + timedelta(days=rng.randint(60, max(span_days, 61)))
            row_created = iso(onboarding_datetime(ctx))
            agreements.append(
                (
                    agreement_id,
                    rid,
                    d6(rate),
                    iso_date(valid_from),
                    iso_date(valid_to) if valid_to else None,
                    row_created,
                    row_created,
                )
            )
            agreements_index.setdefault(rid, []).append((valid_from, valid_to, rate))
            agreement_id += 1
            if valid_to:
                valid_from = valid_to
        if reseller_integration[rid] == "PLATFORM":
            platform_reseller_ids.append(rid)
    conn.executemany("INSERT INTO partnership_agreements VALUES (?,?,?,?,?,?,?)", agreements)
    counts["partnership_agreements"] = len(agreements)

    def commission_rate_for(rid: int, at: date) -> Decimal:
        for vf, vt, rate in agreements_index.get(rid, []):
            if vf <= at and (vt is None or at <= vt):
                return rate
        return Decimal("0")

    # ---- sales_channels (fixed reference list) ----
    channels = [(i + 1, code, name) for i, (code, name) in enumerate(CHANNELS)]
    conn.executemany("INSERT INTO sales_channels VALUES (?,?,?)", channels)
    counts["sales_channels"] = len(channels)

    # ---- orders + order_items ----
    # Each order is for tickets to a single event (its items all share that
    # event's currency -- there's no "organizer" grouping to fall back on
    # for guaranteeing currency consistency across items anymore).
    n_orders = vol["orders"]
    order_batch = []
    item_batch = []
    order_id = 1
    item_id = 1

    def flush():
        nonlocal order_batch, item_batch
        if order_batch:
            conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)", order_batch)
            order_batch = []
        if item_batch:
            conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?,?,?,?,?,?)", item_batch)
            item_batch = []

    for _ in range(n_orders):
        order_dt = sample_order_datetime(ctx, buckets)
        order_date = order_dt.date()

        if platform_reseller_ids and rng.random() < RESELLER_ORDER_RATIO:
            seller_type = "RESELLER"
            rid = rng.choice(platform_reseller_ids)
            channel_id = weighted_choice(rng, list(range(1, 6)), [0.05, 0.1, 0.1, 0.7, 0.05])
        else:
            seller_type = "ORGANIZER"
            rid = None
            channel_id = weighted_choice(rng, list(range(1, 6)), [0.25, 0.35, 0.25, 0.02, 0.13])

        # Feb-2020 spike: prefer CONCERT/FESTIVAL events that month.
        if (order_date.year, order_date.month) == (2020, 2):
            preferred = [e for e in all_event_ids if event_type_of[e] in ("CONCERT", "FESTIVAL")]
            pool = preferred if preferred else all_event_ids
        else:
            pool = all_event_ids
        eid = rng.choice(pool)

        n_items = weighted_choice(rng, ITEM_COUNT_WEIGHTS, ITEM_COUNT_PROBS)
        currency = event_currency[eid]
        commission_rate = commission_rate_for(rid, order_date) if rid else Decimal("0")

        total_amount = Decimal("0")
        total_qty = 0
        tt_options = tt_by_event.get(eid) or []
        for _ in range(n_items):
            if not tt_options:
                continue
            ttid = rng.choice(tt_options)
            qty = rng.randint(1, 6)
            unit_price = tt_face_value[ttid]
            gross = (unit_price * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            commission_amount = (gross * commission_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            created = iso(order_dt)
            item_batch.append(
                (
                    item_id,
                    order_id,
                    eid,
                    ttid,
                    qty,
                    d2(unit_price),
                    d2(gross),
                    d6(commission_rate),
                    d2(commission_amount),
                    created,
                    created,
                )
            )
            total_amount += gross
            total_qty += qty
            item_id += 1

        status = weighted_choice(rng, ORDER_STATUS_CHOICES, ORDER_STATUS_PROBS)
        created = iso(order_dt)
        order_batch.append(
            (
                order_id,
                seller_type,
                rid,
                channel_id,
                created,
                currency,
                d2(total_amount),
                total_qty,
                status,
                created,
                created,
            )
        )
        order_id += 1

        if len(order_batch) >= BATCH_SIZE:
            flush()

    flush()
    counts["orders"] = n_orders
    counts["order_items"] = item_id - 1

    conn.executescript(INDEXES_SQL)
    conn.commit()

    print(f"Generated data/source/b2b.db (profile={settings.profile}, seed={seed})")
    for table in TABLE_ORDER:
        c = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        counts[table] = c
        print(f"  {table:<28} {c:>10,}")
    total_amount_sum = conn.execute("SELECT SUM(CAST(total_amount AS REAL)) FROM orders").fetchone()[0]
    print(f"  {'SUM(orders.total_amount)':<28} {total_amount_sum:>14,.2f}")
    conn.close()


def main() -> int:
    settings = get_settings()
    generate(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
