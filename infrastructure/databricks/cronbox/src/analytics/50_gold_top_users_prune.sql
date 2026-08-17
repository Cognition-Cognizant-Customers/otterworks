-- cron-analytics gold top users, step 1 of 2: prune the run-date slice.
-- The EXISTS guard on silver keeps the empty-input contract: with no events for
-- the run date nothing is deleted and the previously published slice survives,
-- matching the legacy "warn and exit 0 without writing" behaviour. With events
-- present the slice is rebuilt from scratch, so a rerun cannot leave a stale
-- user behind (idempotent republish).
DELETE FROM ow_tp.gold.analytics_daily_top_users
WHERE report_date = :ds
  AND namespace = :ns
  AND EXISTS (
    SELECT 1
    FROM ow_tp.silver.cronbox_events s
    WHERE s.namespace = :ns AND s.report_date = :ds
  );
