-- Phase 7 addition to the meta schema: one row per expectation result per
-- Great Expectations checkpoint run. Deliberately no FK to
-- meta.ingestion_run(run_id) -- `make dq` can run standalone (outside any
-- ingestion run) with its own fresh run_id.

IF OBJECT_ID('meta.data_quality_result', 'U') IS NULL
CREATE TABLE meta.data_quality_result (
    result_id         BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_data_quality_result PRIMARY KEY,
    run_id             UNIQUEIDENTIFIER NOT NULL,
    suite_name         NVARCHAR(200) NOT NULL,
    expectation_type   NVARCHAR(200) NOT NULL,
    column_name        NVARCHAR(200) NULL,
    success            BIT           NOT NULL,
    severity           VARCHAR(20)   NOT NULL CONSTRAINT CK_data_quality_result_severity CHECK (severity IN ('CRITICAL', 'WARN')),
    observed_value     NVARCHAR(MAX) NULL,
    unexpected_count   BIGINT        NULL,
    evaluated_at       DATETIME2(3)  NOT NULL CONSTRAINT DF_data_quality_result_evaluated_at DEFAULT SYSUTCDATETIME()
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_data_quality_result_run_id')
CREATE INDEX IX_data_quality_result_run_id ON meta.data_quality_result(run_id);
GO
