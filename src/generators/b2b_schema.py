"""SQLite DDL for the source B2B operational database (data/source/b2b.db).

organizers and customers were deliberately removed (see NOTES.md "Removing
organizers and customers") -- neither is read by any of the four report
requirements, and organizer_id/customer_id were removed everywhere as bare
keys too, not just their descriptive-attribute tables.

Every table carries created_at / updated_at (TEXT, ISO-8601 UTC, not null) --
these drive incremental ingestion (Phase 5) and later dbt snapshots.
Indexes on updated_at, FKs, and orders.order_ts are created after the bulk
load in generate_b2b.py, not here, per the spec (index-after-load for speed).
"""

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS venues (
    venue_id        INTEGER PRIMARY KEY,
    venue_name      TEXT NOT NULL,
    city            TEXT NOT NULL,
    region          TEXT NOT NULL,
    country         TEXT NOT NULL,
    capacity        INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id        INTEGER PRIMARY KEY,
    venue_id        INTEGER NOT NULL REFERENCES venues(venue_id),
    event_name      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    event_date      TEXT NOT NULL,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_types (
    ticket_type_id   INTEGER PRIMARY KEY,
    event_id         INTEGER NOT NULL REFERENCES events(event_id),
    ticket_type_name TEXT NOT NULL,
    face_value       TEXT NOT NULL,
    currency         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resellers (
    reseller_id      INTEGER PRIMARY KEY,
    reseller_name    TEXT NOT NULL,
    country          TEXT NOT NULL,
    region           TEXT NOT NULL,
    city             TEXT NOT NULL,
    integration_type TEXT NOT NULL,
    is_active        INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS partnership_agreements (
    agreement_id     INTEGER PRIMARY KEY,
    reseller_id      INTEGER NOT NULL REFERENCES resellers(reseller_id),
    commission_rate  TEXT NOT NULL,
    valid_from       TEXT NOT NULL,
    valid_to         TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_channels (
    channel_id       INTEGER PRIMARY KEY,
    channel_code     TEXT NOT NULL,
    channel_name     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id         INTEGER PRIMARY KEY,
    seller_type      TEXT NOT NULL,
    reseller_id      INTEGER REFERENCES resellers(reseller_id),
    channel_id       INTEGER NOT NULL REFERENCES sales_channels(channel_id),
    order_ts         TEXT NOT NULL,
    currency         TEXT NOT NULL,
    total_amount     TEXT NOT NULL,
    total_quantity   INTEGER NOT NULL,
    order_status     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    order_item_id    INTEGER PRIMARY KEY,
    order_id         INTEGER NOT NULL REFERENCES orders(order_id),
    event_id         INTEGER NOT NULL REFERENCES events(event_id),
    ticket_type_id   INTEGER NOT NULL REFERENCES ticket_types(ticket_type_id),
    quantity         INTEGER NOT NULL,
    unit_price       TEXT NOT NULL,
    gross_amount     TEXT NOT NULL,
    commission_rate  TEXT NOT NULL,
    commission_amount TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS ix_venues_updated_at ON venues(updated_at);
CREATE INDEX IF NOT EXISTS ix_events_updated_at ON events(updated_at);
CREATE INDEX IF NOT EXISTS ix_events_venue_id ON events(venue_id);
CREATE INDEX IF NOT EXISTS ix_ticket_types_updated_at ON ticket_types(updated_at);
CREATE INDEX IF NOT EXISTS ix_ticket_types_event_id ON ticket_types(event_id);
CREATE INDEX IF NOT EXISTS ix_resellers_updated_at ON resellers(updated_at);
CREATE INDEX IF NOT EXISTS ix_partnership_agreements_updated_at ON partnership_agreements(updated_at);
CREATE INDEX IF NOT EXISTS ix_partnership_agreements_reseller_id ON partnership_agreements(reseller_id);
CREATE INDEX IF NOT EXISTS ix_orders_updated_at ON orders(updated_at);
CREATE INDEX IF NOT EXISTS ix_orders_order_ts ON orders(order_ts);
CREATE INDEX IF NOT EXISTS ix_orders_reseller_id ON orders(reseller_id);
CREATE INDEX IF NOT EXISTS ix_order_items_updated_at ON order_items(updated_at);
CREATE INDEX IF NOT EXISTS ix_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS ix_order_items_event_id ON order_items(event_id);
CREATE INDEX IF NOT EXISTS ix_order_items_ticket_type_id ON order_items(ticket_type_id);
"""

TABLE_ORDER = [
    "venues",
    "events",
    "ticket_types",
    "resellers",
    "partnership_agreements",
    "sales_channels",
    "orders",
    "order_items",
]

EVENT_TYPES = ["CONCERT", "SPORTS", "THEATRE", "CONFERENCE", "FESTIVAL", "COMEDY"]
CHANNELS = [
    ("ON_SITE", "On-Site Box Office"),
    ("WEB", "Web Storefront"),
    ("MOBILE_APP", "Mobile App"),
    ("PARTNER_API", "Partner API"),
    ("BOX_OFFICE", "Box Office"),
]
CURRENCIES = ["USD", "EUR", "GBP"]
