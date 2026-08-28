-- raw_b2b: one table per source (SQLite) table, same names/columns, typed
-- properly (money as DECIMAL, never FLOAT; timestamps as DATETIME2(3)).
-- created_at/updated_at pass through unchanged from the source. Every table
-- carries the audit columns _run_id / _ingested_at. No FK constraints in
-- raw (see README "Design decisions") -- indexes on FK-shaped columns
-- instead, for join performance without ordering constraints on load.

IF OBJECT_ID('raw_b2b.organizers', 'U') IS NULL
CREATE TABLE raw_b2b.organizers (
    organizer_id    INT           NOT NULL CONSTRAINT PK_raw_organizers PRIMARY KEY,
    organizer_name  NVARCHAR(200) NOT NULL,
    country         NVARCHAR(10)  NOT NULL,
    region          NVARCHAR(100) NOT NULL,
    city            NVARCHAR(100) NOT NULL,
    is_active       BIT           NOT NULL,
    created_at      DATETIME2(3)  NOT NULL,
    updated_at      DATETIME2(3)  NOT NULL,
    _run_id         UNIQUEIDENTIFIER NULL,
    _ingested_at    DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_organizers_ingested_at DEFAULT SYSUTCDATETIME()
);
GO

IF OBJECT_ID('raw_b2b.venues', 'U') IS NULL
CREATE TABLE raw_b2b.venues (
    venue_id        INT           NOT NULL CONSTRAINT PK_raw_venues PRIMARY KEY,
    organizer_id    INT           NOT NULL,
    venue_name      NVARCHAR(200) NOT NULL,
    city            NVARCHAR(100) NOT NULL,
    region          NVARCHAR(100) NOT NULL,
    country         NVARCHAR(10)  NOT NULL,
    capacity        INT           NOT NULL,
    created_at      DATETIME2(3)  NOT NULL,
    updated_at      DATETIME2(3)  NOT NULL,
    _run_id         UNIQUEIDENTIFIER NULL,
    _ingested_at    DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_venues_ingested_at DEFAULT SYSUTCDATETIME()
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_venues_organizer_id')
CREATE INDEX IX_raw_venues_organizer_id ON raw_b2b.venues(organizer_id);
GO

IF OBJECT_ID('raw_b2b.events', 'U') IS NULL
CREATE TABLE raw_b2b.events (
    event_id        INT           NOT NULL CONSTRAINT PK_raw_events PRIMARY KEY,
    venue_id        INT           NOT NULL,
    organizer_id    INT           NOT NULL,
    event_name      NVARCHAR(300) NOT NULL,
    event_type      VARCHAR(20)   NOT NULL,
    event_date      DATE          NOT NULL,
    status          VARCHAR(20)   NOT NULL,
    created_at      DATETIME2(3)  NOT NULL,
    updated_at      DATETIME2(3)  NOT NULL,
    _run_id         UNIQUEIDENTIFIER NULL,
    _ingested_at    DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_events_ingested_at DEFAULT SYSUTCDATETIME()
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_events_venue_id')
CREATE INDEX IX_raw_events_venue_id ON raw_b2b.events(venue_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_events_organizer_id')
CREATE INDEX IX_raw_events_organizer_id ON raw_b2b.events(organizer_id);
GO

IF OBJECT_ID('raw_b2b.ticket_types', 'U') IS NULL
CREATE TABLE raw_b2b.ticket_types (
    ticket_type_id   INT           NOT NULL CONSTRAINT PK_raw_ticket_types PRIMARY KEY,
    event_id         INT           NOT NULL,
    ticket_type_name NVARCHAR(100) NOT NULL,
    face_value       DECIMAL(18,4) NOT NULL,
    currency         CHAR(3)       NOT NULL,
    created_at       DATETIME2(3)  NOT NULL,
    updated_at       DATETIME2(3)  NOT NULL,
    _run_id          UNIQUEIDENTIFIER NULL,
    _ingested_at     DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_ticket_types_ingested_at DEFAULT SYSUTCDATETIME()
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_ticket_types_event_id')
CREATE INDEX IX_raw_ticket_types_event_id ON raw_b2b.ticket_types(event_id);
GO

IF OBJECT_ID('raw_b2b.resellers', 'U') IS NULL
CREATE TABLE raw_b2b.resellers (
    reseller_id      INT           NOT NULL CONSTRAINT PK_raw_resellers PRIMARY KEY,
    reseller_name    NVARCHAR(200) NOT NULL,
    country          NVARCHAR(10)  NOT NULL,
    region           NVARCHAR(100) NOT NULL,
    city             NVARCHAR(100) NOT NULL,
    integration_type VARCHAR(20)   NOT NULL,
    is_active        BIT           NOT NULL,
    created_at       DATETIME2(3)  NOT NULL,
    updated_at       DATETIME2(3)  NOT NULL,
    _run_id          UNIQUEIDENTIFIER NULL,
    _ingested_at     DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_resellers_ingested_at DEFAULT SYSUTCDATETIME()
);
GO

IF OBJECT_ID('raw_b2b.partnership_agreements', 'U') IS NULL
CREATE TABLE raw_b2b.partnership_agreements (
    agreement_id     INT           NOT NULL CONSTRAINT PK_raw_partnership_agreements PRIMARY KEY,
    organizer_id     INT           NOT NULL,
    reseller_id      INT           NOT NULL,
    commission_rate  DECIMAL(9,6)  NOT NULL,
    valid_from       DATE          NOT NULL,
    valid_to         DATE          NULL,
    created_at       DATETIME2(3)  NOT NULL,
    updated_at       DATETIME2(3)  NOT NULL,
    _run_id          UNIQUEIDENTIFIER NULL,
    _ingested_at     DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_partnership_agreements_ingested_at DEFAULT SYSUTCDATETIME()
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_partnership_agreements_org_reseller')
CREATE INDEX IX_raw_partnership_agreements_org_reseller ON raw_b2b.partnership_agreements(organizer_id, reseller_id);
GO

IF OBJECT_ID('raw_b2b.customers', 'U') IS NULL
CREATE TABLE raw_b2b.customers (
    customer_id      INT           NOT NULL CONSTRAINT PK_raw_customers PRIMARY KEY,
    first_name       NVARCHAR(100) NOT NULL,
    last_name        NVARCHAR(100) NOT NULL,
    email            NVARCHAR(320) NOT NULL,
    country          NVARCHAR(10)  NOT NULL,
    region           NVARCHAR(100) NOT NULL,
    city             NVARCHAR(100) NOT NULL,
    created_at       DATETIME2(3)  NOT NULL,
    updated_at       DATETIME2(3)  NOT NULL,
    _run_id          UNIQUEIDENTIFIER NULL,
    _ingested_at     DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_customers_ingested_at DEFAULT SYSUTCDATETIME()
);
GO

IF OBJECT_ID('raw_b2b.sales_channels', 'U') IS NULL
CREATE TABLE raw_b2b.sales_channels (
    channel_id       INT           NOT NULL CONSTRAINT PK_raw_sales_channels PRIMARY KEY,
    channel_code     VARCHAR(20)   NOT NULL,
    channel_name     NVARCHAR(100) NOT NULL,
    _run_id          UNIQUEIDENTIFIER NULL,
    _ingested_at     DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_sales_channels_ingested_at DEFAULT SYSUTCDATETIME()
);
GO

IF OBJECT_ID('raw_b2b.orders', 'U') IS NULL
CREATE TABLE raw_b2b.orders (
    order_id         INT           NOT NULL CONSTRAINT PK_raw_orders PRIMARY KEY,
    customer_id      INT           NOT NULL,
    seller_type      VARCHAR(20)   NOT NULL,
    organizer_id     INT           NOT NULL,
    reseller_id      INT           NULL,
    channel_id       INT           NOT NULL,
    order_ts         DATETIME2(3)  NOT NULL,
    currency         CHAR(3)       NOT NULL,
    total_amount     DECIMAL(18,4) NOT NULL,
    total_quantity   INT           NOT NULL,
    order_status     VARCHAR(20)   NOT NULL,
    created_at       DATETIME2(3)  NOT NULL,
    updated_at       DATETIME2(3)  NOT NULL,
    _run_id          UNIQUEIDENTIFIER NULL,
    _ingested_at     DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_orders_ingested_at DEFAULT SYSUTCDATETIME()
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_orders_customer_id')
CREATE INDEX IX_raw_orders_customer_id ON raw_b2b.orders(customer_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_orders_organizer_id')
CREATE INDEX IX_raw_orders_organizer_id ON raw_b2b.orders(organizer_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_orders_reseller_id')
CREATE INDEX IX_raw_orders_reseller_id ON raw_b2b.orders(reseller_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_orders_order_ts')
CREATE INDEX IX_raw_orders_order_ts ON raw_b2b.orders(order_ts);
GO

IF OBJECT_ID('raw_b2b.order_items', 'U') IS NULL
CREATE TABLE raw_b2b.order_items (
    order_item_id     INT           NOT NULL CONSTRAINT PK_raw_order_items PRIMARY KEY,
    order_id          INT           NOT NULL,
    event_id          INT           NOT NULL,
    ticket_type_id    INT           NOT NULL,
    quantity          INT           NOT NULL,
    unit_price        DECIMAL(18,4) NOT NULL,
    gross_amount      DECIMAL(18,4) NOT NULL,
    commission_rate   DECIMAL(9,6)  NOT NULL,
    commission_amount DECIMAL(18,4) NOT NULL,
    created_at        DATETIME2(3)  NOT NULL,
    updated_at        DATETIME2(3)  NOT NULL,
    _run_id           UNIQUEIDENTIFIER NULL,
    _ingested_at      DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_order_items_ingested_at DEFAULT SYSUTCDATETIME()
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_order_items_order_id')
CREATE INDEX IX_raw_order_items_order_id ON raw_b2b.order_items(order_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_order_items_event_id')
CREATE INDEX IX_raw_order_items_event_id ON raw_b2b.order_items(event_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_order_items_ticket_type_id')
CREATE INDEX IX_raw_order_items_ticket_type_id ON raw_b2b.order_items(ticket_type_id);
GO
