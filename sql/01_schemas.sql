-- Medallion schemas. CREATE SCHEMA must be the only statement in its batch,
-- so each one gets its own GO-delimited block (see src/common/init_db.py).

IF SCHEMA_ID('raw_b2b') IS NULL
    EXEC('CREATE SCHEMA raw_b2b');
GO

IF SCHEMA_ID('raw_reseller') IS NULL
    EXEC('CREATE SCHEMA raw_reseller');
GO

IF SCHEMA_ID('meta') IS NULL
    EXEC('CREATE SCHEMA meta');
GO

-- dwh and datamart are created empty here. Scope stops at raw + a dbt
-- skeleton (Phase 9); no models are written against them in this build.
IF SCHEMA_ID('dwh') IS NULL
    EXEC('CREATE SCHEMA dwh');
GO

IF SCHEMA_ID('datamart') IS NULL
    EXEC('CREATE SCHEMA datamart');
GO
