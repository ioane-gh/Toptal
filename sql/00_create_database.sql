-- Creates the project database if it doesn't exist, and applies the sizing /
-- recovery settings this load-heavy pipeline depends on. Runs against
-- `master` with AUTOCOMMIT (CREATE/ALTER DATABASE cannot run inside a
-- transaction), one statement at a time -- pyodbc has no GO batch separator,
-- and T-SQL variables do not survive across separate execute() calls, so
-- this script deliberately avoids DECLARE/dynamic SQL: every statement is
-- self-contained.
--
-- Placeholders ($(DB_NAME), $(DATA_DIR), etc.) are substituted by
-- src/common/init_db.py before each statement is sent.
--
-- RECOVERY SIMPLE: this is a load-heavy development database with a fully
--   reproducible source (the generators). Bulk-loading millions of rows
--   under the default FULL recovery model grows the transaction log until
--   the disk fills; SIMPLE truncates the log at each checkpoint. Point-in-
--   time recovery buys nothing here.
-- Pre-sized files with fixed-MB growth: SQL Server's defaults (8MB data,
--   64MB log, percentage growth) force hundreds of autogrow events during a
--   large load, each one a pause, and fragment the files. Pre-allocating
--   and growing in fixed chunks avoids both.
-- READ_COMMITTED_SNAPSHOT ON: ingestion workers write while Great
--   Expectations reads concurrently. Snapshot isolation lets readers avoid
--   blocking on writers instead of taking shared locks.

-- STATEMENT: create_with_paths (used only when DATA_DIR/LOG_DIR are set)
IF DB_ID('$(DB_NAME)') IS NULL
CREATE DATABASE [$(DB_NAME)]
ON PRIMARY (
    NAME = N'$(DB_NAME)_data',
    FILENAME = N'$(DATA_DIR)\$(DB_NAME).mdf',
    SIZE = $(DATA_FILE_SIZE_MB)MB, FILEGROWTH = $(DATA_FILE_GROWTH_MB)MB
)
LOG ON (
    NAME = N'$(DB_NAME)_log',
    FILENAME = N'$(LOG_DIR)\$(DB_NAME)_log.ldf',
    SIZE = $(LOG_FILE_SIZE_MB)MB, FILEGROWTH = $(LOG_FILE_GROWTH_MB)MB
);

-- STATEMENT: create_default_path (used only when DATA_DIR/LOG_DIR are empty)
IF DB_ID('$(DB_NAME)') IS NULL
CREATE DATABASE [$(DB_NAME)];

-- STATEMENT: resize_default_data_file (used only when DATA_DIR/LOG_DIR are empty)
-- SQL Server names the default primary data file after the database itself.
ALTER DATABASE [$(DB_NAME)] MODIFY FILE (NAME = N'$(DB_NAME)', SIZE = $(DATA_FILE_SIZE_MB)MB, FILEGROWTH = $(DATA_FILE_GROWTH_MB)MB);

-- STATEMENT: resize_default_log_file (used only when DATA_DIR/LOG_DIR are empty)
ALTER DATABASE [$(DB_NAME)] MODIFY FILE (NAME = N'$(DB_NAME)_log', SIZE = $(LOG_FILE_SIZE_MB)MB, FILEGROWTH = $(LOG_FILE_GROWTH_MB)MB);

-- STATEMENT: set_recovery_simple
ALTER DATABASE [$(DB_NAME)] SET RECOVERY SIMPLE;

-- STATEMENT: set_read_committed_snapshot
ALTER DATABASE [$(DB_NAME)] SET READ_COMMITTED_SNAPSHOT ON;
