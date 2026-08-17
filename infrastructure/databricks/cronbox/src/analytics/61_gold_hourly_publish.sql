-- cron-analytics gold hourly breakdown, step 2 of 2: publish the run-date slice.
-- Replaces analytics/daily/**/hourly_breakdown.json.gz: the legacy nested
-- {hour: {event_type: count}} object becomes one row per (hour, event_type).
INSERT INTO ow_tp.gold.analytics_hourly_breakdown
SELECT
  report_date,
  namespace,
  event_hour,
  event_type,
  COUNT(*) AS event_count,
  current_timestamp() AS updated_at
FROM ow_tp.silver.cronbox_events
WHERE namespace = :ns AND report_date = :ds
GROUP BY report_date, namespace, event_hour, event_type;
