-- raw_reseller.daily_sales: the file lands as text. Every business column is
-- NVARCHAR(255) -- typing and cleansing happen downstream in dwh, not here.
-- `id` is a technical surrogate PK (nonclustered); the clustered index the
-- spec asks for sits on (_source_file_name, _source_row_number), which is
-- how the table is actually queried and re-loaded per file.

IF OBJECT_ID('raw_reseller.daily_sales', 'U') IS NULL
CREATE TABLE raw_reseller.daily_sales (
    id                  BIGINT IDENTITY(1,1) NOT NULL,

    ticket_id           NVARCHAR(255) NULL,
    order_id            NVARCHAR(255) NULL,
    event_id            NVARCHAR(255) NULL,
    event_name          NVARCHAR(255) NULL,
    event_type          NVARCHAR(255) NULL,
    event_date          NVARCHAR(255) NULL,
    venue_name          NVARCHAR(255) NULL,
    venue_city          NVARCHAR(255) NULL,
    venue_region        NVARCHAR(255) NULL,
    venue_country       NVARCHAR(255) NULL,
    ticket_type         NVARCHAR(255) NULL,
    quantity            NVARCHAR(255) NULL,
    unit_price          NVARCHAR(255) NULL,
    total_amount        NVARCHAR(255) NULL,
    currency            NVARCHAR(255) NULL,
    sale_date           NVARCHAR(255) NULL,
    sale_channel        NVARCHAR(255) NULL,
    customer_id         NVARCHAR(255) NULL,
    customer_email      NVARCHAR(255) NULL,
    customer_country    NVARCHAR(255) NULL,
    customer_city       NVARCHAR(255) NULL,
    reseller_id         NVARCHAR(255) NULL,
    reseller_name       NVARCHAR(255) NULL,
    order_status        NVARCHAR(255) NULL,

    _source_file_name   NVARCHAR(400) NOT NULL,
    _source_row_number  INT           NOT NULL,
    _file_reseller_id   NVARCHAR(50)  NOT NULL,
    _file_sale_date     DATE          NOT NULL,
    _run_id             UNIQUEIDENTIFIER NULL,
    _ingested_at        DATETIME2(3)  NOT NULL CONSTRAINT DF_raw_daily_sales_ingested_at DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_raw_daily_sales PRIMARY KEY NONCLUSTERED (id)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_daily_sales_file_row' AND object_id = OBJECT_ID('raw_reseller.daily_sales'))
CREATE CLUSTERED INDEX IX_raw_daily_sales_file_row ON raw_reseller.daily_sales(_source_file_name, _source_row_number);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_raw_daily_sales_reseller_id' AND object_id = OBJECT_ID('raw_reseller.daily_sales'))
CREATE INDEX IX_raw_daily_sales_reseller_id ON raw_reseller.daily_sales(_file_reseller_id);
GO
