-- cron-analytics gold hourly breakdown, step 1 of 2: prune the run-date slice.
-- Same EXISTS guard as top_users: empty input rewrites nothing, a rerun with
-- events republishes the slice exactly.
DELETE FROM ow_tp.gold.analytics_hourly_breakdown
WHERE report_date = :ds
  AND namespace = :ns
  AND EXISTS (
    SELECT 1
    FROM ow_tp.silver.cronbox_events s
    WHERE s.namespace = :ns AND s.report_date = :ds
  );
