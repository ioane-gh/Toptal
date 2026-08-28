-- Run-control / processing-metadata tables. Four tables, per Phase 4 --
-- meta.data_quality_result is added later by sql/05_dq_meta_table.sql
-- (Phase 7), not here, so the schema matches each phase's acceptance
-- criteria exactly at the point it's checked.

IF OBJECT_ID('meta.ingestion_run', 'U') IS NULL
CREATE TABLE meta.ingestion_run (
    run_id          UNIQUEIDENTIFIER NOT NULL CONSTRAINT PK_ingestion_run PRIMARY KEY DEFAULT NEWID(),
    run_type        VARCHAR(20)      NOT NULL CONSTRAINT CK_ingestion_run_type CHECK (run_type IN ('FULL', 'INCREMENTAL')),
    source_filter   NVARCHAR(200)    NULL,
    started_at      DATETIME2(3)     NOT NULL,
    finished_at     DATETIME2(3)     NULL,
    status          VARCHAR(20)      NOT NULL CONSTRAINT CK_ingestion_run_status CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')),
    rows_inserted   BIGINT           NOT NULL CONSTRAINT DF_ingestion_run_rows_inserted DEFAULT 0,
    rows_skipped    BIGINT           NOT NULL CONSTRAINT DF_ingestion_run_rows_skipped DEFAULT 0,
    log_file        NVARCHAR(500)    NULL,
    error_message   NVARCHAR(MAX)    NULL
);
GO

IF OBJECT_ID('meta.ingestion_job', 'U') IS NULL
CREATE TABLE meta.ingestion_job (
    job_id          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ingestion_job PRIMARY KEY,
    run_id          UNIQUEIDENTIFIER NOT NULL CONSTRAINT FK_ingestion_job_run REFERENCES meta.ingestion_run(run_id),
    job_name        NVARCHAR(200)    NOT NULL,
    source_system   VARCHAR(20)      NOT NULL,
    source_object   NVARCHAR(200)    NOT NULL,
    target_object   NVARCHAR(200)    NOT NULL,
    load_mode       VARCHAR(20)      NOT NULL CONSTRAINT CK_ingestion_job_load_mode CHECK (load_mode IN ('FULL', 'INCREMENTAL')),
    status          VARCHAR(20)      NOT NULL CONSTRAINT CK_ingestion_job_status CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    started_at      DATETIME2(3)     NOT NULL,
    finished_at     DATETIME2(3)     NULL,
    duration_sec    DECIMAL(12,3)    NULL,
    rows_read       BIGINT           NOT NULL CONSTRAINT DF_ingestion_job_rows_read DEFAULT 0,
    rows_inserted   BIGINT           NOT NULL CONSTRAINT DF_ingestion_job_rows_inserted DEFAULT 0,
    rows_updated    BIGINT           NOT NULL CONSTRAINT DF_ingestion_job_rows_updated DEFAULT 0,
    rows_skipped    BIGINT           NOT NULL CONSTRAINT DF_ingestion_job_rows_skipped DEFAULT 0,
    error_message   NVARCHAR(MAX)    NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ingestion_job_run_id')
CREATE INDEX IX_ingestion_job_run_id ON meta.ingestion_job(run_id);
GO

IF OBJECT_ID('meta.watermark', 'U') IS NULL
CREATE TABLE meta.watermark (
    source_system     VARCHAR(20)   NOT NULL,
    source_object      NVARCHAR(200) NOT NULL,
    watermark_column    NVARCHAR(100) NOT NULL,
    watermark_value     DATETIME2(3)  NOT NULL,
    last_run_id         UNIQUEIDENTIFIER NULL,
    updated_at           DATETIME2(3)  NOT NULL,
    CONSTRAINT PK_watermark PRIMARY KEY (source_system, source_object)
);
GO

IF OBJECT_ID('meta.processed_file', 'U') IS NULL
CREATE TABLE meta.processed_file (
    file_id          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_processed_file PRIMARY KEY,
    file_name        NVARCHAR(400) NOT NULL,
    reseller_id      NVARCHAR(50)  NULL,
    sale_date        DATE          NULL,
    file_size_bytes  BIGINT        NULL,
    file_modified_at DATETIME2(3)  NULL,
    row_count        BIGINT        NULL,
    rows_inserted    BIGINT        NULL,
    rows_skipped     BIGINT        NULL,
    status           VARCHAR(30)   NOT NULL CONSTRAINT CK_processed_file_status CHECK (status IN ('IN_PROGRESS', 'PROCESSED', 'FAILED', 'SKIPPED_INVALID_NAME')),
    processed_at     DATETIME2(3)  NULL,
    run_id           UNIQUEIDENTIFIER NULL CONSTRAINT FK_processed_file_run REFERENCES meta.ingestion_run(run_id),
    CONSTRAINT UQ_processed_file_file_name UNIQUE (file_name)
);
GO
