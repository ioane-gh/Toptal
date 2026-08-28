"""Generates daily reseller CSV exports into data/reseller/ (never moved --
see README). Reads config/reseller_file_schema.yaml as the single source of
truth for column layout, and data/source/b2b.db for the real third-party
reseller_id/name pairs so RESELLER_ID in the files joins to
partnership_agreements downstream (see NOTES.md, R3).

Events inside the files are NOT the B2B DB's -- they belong to the
third-party system -- but use the same event-type and region vocabulary so
reports read consistently across both sources. There is no customer data in
the contract at all (see NOTES.md "Removing organizers and customers") --
neither the B2B nor the reseller side carries it, since no report
requirement reads a customer attribute.

Deliberate defects (config generation.defects) are injected at configured
rates and each type is guaranteed to occur at least once. A few fully
special-cased files (malformed name, header-only, latin-1 encoding) are
always produced regardless of defect rates, as required by Phase 3.
"""
from __future__ import annotations

import csv
import random
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from faker import Faker

from src.common.config import Settings, get_settings

DELTA_DAYS = 7  # how many new days --delta appends after the historical range
FILE_PROBABILITY = 0.70  # chance a given reseller files on a given day


def load_schema(settings: Settings) -> dict:
    path = settings.repo_root / "config" / "reseller_file_schema.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_third_party_resellers(settings: Settings) -> list[tuple[int, str]]:
    db_path = settings.path("sqlite_db")
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} does not exist -- run `make gen-b2b` first")
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT reseller_id, reseller_name FROM resellers WHERE integration_type='THIRD_PARTY' ORDER BY reseller_id"
    ).fetchall()
    conn.close()
    if not rows:
        raise RuntimeError("No THIRD_PARTY resellers found in b2b.db")
    return rows


@dataclass
class ThirdPartyUniverse:
    events: list[dict]  # EVENT_ID, EVENT_NAME, EVENT_TYPE, VENUE_*


def build_universe(rng: random.Random, fake: Faker, schema: dict, n_events: int) -> ThirdPartyUniverse:
    events = []
    for i in range(1, n_events + 1):
        events.append(
            {
                "EVENT_ID": f"TP-EVT-{i:06d}",
                "EVENT_NAME": fake.catch_phrase(),
                "EVENT_TYPE": rng.choice(schema["allowed_event_types"]),
                "VENUE_NAME": fake.company() + " Hall",
                "VENUE_CITY": fake.city(),
                "VENUE_REGION": fake.state() if hasattr(fake, "state") else fake.city(),
                "VENUE_COUNTRY": fake.country_code(),
            }
        )
    return ThirdPartyUniverse(events=events)


TICKET_TYPE_NAMES = ["General Admission", "VIP", "Early Bird", "Premium", "Standing"]


def gen_row(rng: random.Random, fake: Faker, schema: dict, universe: ThirdPartyUniverse, reseller_id: int, reseller_name: str, sale_date: date, ticket_seq: int) -> dict:
    event = rng.choice(universe.events)
    quantity = rng.randint(1, 6)
    unit_price = round(rng.uniform(15.0, 250.0), 2)
    total_amount = round(unit_price * quantity, 2)
    sale_dt = datetime(sale_date.year, sale_date.month, sale_date.day, rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
    event_date = sale_date + timedelta(days=rng.randint(0, 60))

    return {
        "TICKET_ID": f"TKT-{reseller_id}-{sale_date.strftime('%Y%m%d')}-{ticket_seq:06d}",
        "ORDER_ID": f"ORD-{reseller_id}-{sale_date.strftime('%Y%m%d')}-{(ticket_seq - 1) // 2 + 1:06d}",
        "EVENT_ID": event["EVENT_ID"],
        "EVENT_NAME": event["EVENT_NAME"],
        "EVENT_TYPE": event["EVENT_TYPE"],
        "EVENT_DATE": event_date.strftime("%Y-%m-%d"),
        "VENUE_NAME": event["VENUE_NAME"],
        "VENUE_CITY": event["VENUE_CITY"],
        "VENUE_REGION": event["VENUE_REGION"],
        "VENUE_COUNTRY": event["VENUE_COUNTRY"],
        "TICKET_TYPE": rng.choice(TICKET_TYPE_NAMES),
        "QUANTITY": str(quantity),
        "UNIT_PRICE": f"{unit_price:.2f}",
        "TOTAL_AMOUNT": f"{total_amount:.2f}",
        "CURRENCY": rng.choice(schema["allowed_currencies"]),
        "SALE_DATE": sale_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "SALE_CHANNEL": rng.choice(["ON_SITE", "WEB", "MOBILE_APP", "PARTNER_API", "BOX_OFFICE"]),
        "RESELLER_ID": str(reseller_id),
        "RESELLER_NAME": reseller_name,
        "ORDER_STATUS": rng.choices(schema["allowed_order_status"], weights=[0.85, 0.10, 0.05], k=1)[0],
    }


DEFECT_APPLIERS = {}


def defect(name):
    def wrap(fn):
        DEFECT_APPLIERS[name] = fn
        return fn

    return wrap


@defect("missing_required")
def _d_missing_required(rng, row, columns):
    required = [c["name"] for c in columns if not c["nullable"]]
    field_name = rng.choice(required)
    row[field_name] = ""


@defect("bad_date")
def _d_bad_date(rng, row, columns):
    field_name = rng.choice(["EVENT_DATE", "SALE_DATE"])
    row[field_name] = rng.choice(["2020-13-45", "31/02/2020", "not-a-date"])


@defect("negative_quantity")
def _d_negative_quantity(rng, row, columns):
    row["QUANTITY"] = str(rng.choice([0, -1, -5]))


@defect("bad_number")
def _d_bad_number(rng, row, columns):
    row["UNIT_PRICE"] = rng.choice(["abc", "12,50", "€12.50"])


@defect("amount_mismatch")
def _d_amount_mismatch(rng, row, columns):
    row["TOTAL_AMOUNT"] = f"{float(row['TOTAL_AMOUNT']) + rng.uniform(10, 100):.2f}"


@defect("column_count_mismatch")
def _d_column_count_mismatch(rng, row, columns):
    row["__column_count_defect__"] = rng.choice(["drop_last", "extra"])


def apply_whitespace_casing(rng: random.Random, row: dict) -> None:
    field_name = rng.choice(["EVENT_NAME", "VENUE_NAME", "RESELLER_NAME", "TICKET_TYPE"])
    val = row[field_name]
    style = rng.choice(["upper", "lower", "title"])
    val = {"upper": val.upper(), "lower": val.lower(), "title": val.title()}[style]
    row[field_name] = f"   {val}   "


@dataclass
class FileSpec:
    reseller_id: int
    reseller_name: str
    sale_date: date
    rows: list[dict] = field(default_factory=list)
    encoding: str = "utf-8"
    header_only: bool = False
    malformed_name: str | None = None


def write_file(path: Path, spec: FileSpec, columns: list[dict]) -> int:
    col_names = [c["name"] for c in columns]
    with open(path, "w", encoding=spec.encoding, newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(col_names)
        if spec.header_only:
            return 0
        for row in spec.rows:
            defect_mode = row.pop("__column_count_defect__", None)
            values = [row.get(c, "") for c in col_names]
            if defect_mode == "drop_last":
                values = values[:-1]
            elif defect_mode == "extra":
                values = values + ["EXTRA"]
            writer.writerow(values)
        return len(spec.rows)


def generate(settings: Settings, delta: bool) -> None:
    rng = random.Random(settings.seed + (3 if delta else 2))
    fake = Faker()
    Faker.seed(settings.seed + (3 if delta else 2))

    schema = load_schema(settings)
    columns = schema["columns"]
    resellers = load_third_party_resellers(settings)

    date_range = settings.get("generation.date_range")
    date_start = datetime.strptime(date_range["start"], "%Y-%m-%d").date()
    date_end = datetime.strptime(date_range["end"], "%Y-%m-%d").date()

    if delta:
        window_start = date_end + timedelta(days=1)
        window_end = window_start + timedelta(days=DELTA_DAYS - 1)
    else:
        window_start, window_end = date_start, date_end

    vol = settings.profile_volumes
    n_events = max(20, vol["events"] // 5)
    universe = build_universe(rng, fake, schema, n_events)

    out_dir = settings.path("reseller_dir")
    out_dir.mkdir(parents=True, exist_ok=True)

    defect_cfg = settings.get("generation.defects")
    defect_counts = {k: 0 for k in defect_cfg}

    file_specs: list[FileSpec] = []
    ticket_seq_counter = 0
    d = window_start
    while d <= window_end:
        for reseller_id, reseller_name in resellers:
            if rng.random() > FILE_PROBABILITY:
                continue  # gap day -- not every reseller files every day
            n_rows = rng.randint(10, 150)
            rows = []
            for _ in range(n_rows):
                ticket_seq_counter += 1
                rows.append(gen_row(rng, fake, schema, universe, reseller_id, reseller_name, d, ticket_seq_counter))
            file_specs.append(FileSpec(reseller_id=reseller_id, reseller_name=reseller_name, sale_date=d, rows=rows))
        d += timedelta(days=1)

    # One oversized file to demonstrate chunked reading.
    large_rows_n = vol["large_file_rows"]
    big_reseller_id, big_reseller_name = resellers[0]
    big_date = window_start + timedelta(days=min(3, (window_end - window_start).days))
    big_rows = []
    for _ in range(large_rows_n):
        ticket_seq_counter += 1
        big_rows.append(gen_row(rng, fake, schema, universe, big_reseller_id, big_reseller_name, big_date, ticket_seq_counter))
    file_specs.append(FileSpec(reseller_id=big_reseller_id, reseller_name=big_reseller_name, sale_date=big_date, rows=big_rows))

    # ---- probabilistic per-row defect injection across the whole corpus ----
    # At most one row-level defect per row: independently rolling each type
    # against the same row can stack incompatible mutations (e.g. amount_mismatch
    # trying to parse a value that missing_required just blanked out).
    row_defect_names = [n for n in defect_cfg if n != "duplicate_ticket_id"]
    row_defect_probs = [defect_cfg[n] for n in row_defect_names]
    for spec in file_specs:
        for row in spec.rows:
            roll = rng.random()
            cumulative = 0.0
            for name, prob in zip(row_defect_names, row_defect_probs):
                cumulative += prob
                if roll < cumulative:
                    DEFECT_APPLIERS[name](rng, row, columns)
                    defect_counts[name] += 1
                    break
            if rng.random() < 0.02:
                apply_whitespace_casing(rng, row)

    # duplicate_ticket_id: pick files with >=2 rows and force a duplicate.
    eligible = [s for s in file_specs if len(s.rows) >= 2]
    n_dupe_files = max(1, round(len(eligible) * defect_cfg["duplicate_ticket_id"]))
    for spec in rng.sample(eligible, min(n_dupe_files, len(eligible))):
        spec.rows[1]["TICKET_ID"] = spec.rows[0]["TICKET_ID"]
        defect_counts["duplicate_ticket_id"] += 1

    # Guarantee every defect type occurred at least once.
    if defect_counts["duplicate_ticket_id"] == 0 and eligible:
        spec = eligible[0]
        spec.rows[1]["TICKET_ID"] = spec.rows[0]["TICKET_ID"]
        defect_counts["duplicate_ticket_id"] += 1
    for name in defect_cfg:
        if name == "duplicate_ticket_id":
            continue
        if defect_counts[name] == 0:
            target = next((s for s in file_specs if s.rows), None)
            if target:
                DEFECT_APPLIERS[name](rng, target.rows[0], columns)
                defect_counts[name] += 1

    # ---- always-present special-case files (not probability-driven) ----
    header_only_reseller_id, header_only_reseller_name = resellers[-1]
    header_only_date = window_start + timedelta(days=1)
    file_specs.append(
        FileSpec(reseller_id=header_only_reseller_id, reseller_name=header_only_reseller_name, sale_date=header_only_date, rows=[], header_only=True)
    )

    latin1_reseller_id, latin1_reseller_name = resellers[min(1, len(resellers) - 1)]
    latin1_date = window_start + timedelta(days=2)
    latin1_rows = [gen_row(rng, fake, schema, universe, latin1_reseller_id, latin1_reseller_name, latin1_date, 900001 + i) for i in range(5)]
    latin1_rows[0]["VENUE_CITY"] = "São Paulo"  # non-ASCII, forces the latin-1 fallback path
    file_specs.append(
        FileSpec(reseller_id=latin1_reseller_id, reseller_name=latin1_reseller_name, sale_date=latin1_date, rows=latin1_rows, encoding="latin-1")
    )

    malformed_reseller_id = resellers[0][0]
    malformed_spec = FileSpec(
        reseller_id=malformed_reseller_id,
        reseller_name=resellers[0][1],
        sale_date=window_start,
        rows=[gen_row(rng, fake, schema, universe, malformed_reseller_id, resellers[0][1], window_start, 900101)],
        malformed_name=f"DailySales_{window_start.isoformat()}_R{malformed_reseller_id}.CSV",
    )
    file_specs.append(malformed_spec)

    # ---- write everything out ----
    # Dedupe on output filename first (special-cased files appended last win
    # a same-slot collision with a random gap-day file) so the printed
    # counts reflect exactly what lands on disk.
    by_filename: dict[str, FileSpec] = {}
    for spec in file_specs:
        fname = spec.malformed_name or f"DailySales_{spec.sale_date.strftime('%m%d%Y')}_{spec.reseller_id}.CSV"
        by_filename[fname] = spec

    total_rows = 0
    total_bytes = 0
    file_count = 0
    for fname, spec in by_filename.items():
        path = out_dir / fname
        n = write_file(path, spec, columns)
        total_rows += n
        total_bytes += path.stat().st_size
        file_count += 1

    print(f"Generated {file_count} reseller files into {out_dir} (delta={delta}, profile={settings.profile})")
    print(f"  total_rows:  {total_rows:,}")
    print(f"  total_bytes: {total_bytes:,}")
    print("  defect counts:")
    for name, n in defect_counts.items():
        print(f"    {name:<24} {n:>6,}")
    print("  special files: 1 header-only, 1 latin-1, 1 malformed-name, 1 oversized")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate reseller daily-sales CSV files.")
    parser.add_argument("--delta", action="store_true", help="Generate only new files dated after the historical range.")
    args = parser.parse_args()

    settings = get_settings()
    generate(settings, delta=args.delta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
