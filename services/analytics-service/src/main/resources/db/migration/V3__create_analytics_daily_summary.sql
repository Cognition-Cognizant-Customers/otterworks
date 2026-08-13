-- Daily platform-wide summary maintained by the analytics ETL pipeline
-- (one row per day, idempotently upserted on report_date).

CREATE TABLE IF NOT EXISTS analytics_daily_summary (
    report_date       DATE PRIMARY KEY,
    active_users      BIGINT NOT NULL DEFAULT 0,
    active_documents  BIGINT NOT NULL DEFAULT 0,
    active_files      BIGINT NOT NULL DEFAULT 0,
    total_events      BIGINT NOT NULL DEFAULT 0,
    documents_created BIGINT NOT NULL DEFAULT 0,
    documents_edited  BIGINT NOT NULL DEFAULT 0,
    comments_added    BIGINT NOT NULL DEFAULT 0,
    files_uploaded    BIGINT NOT NULL DEFAULT 0,
    files_shared      BIGINT NOT NULL DEFAULT 0,
    files_deleted     BIGINT NOT NULL DEFAULT 0,
    bytes_uploaded    BIGINT NOT NULL DEFAULT 0,
    updated_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
