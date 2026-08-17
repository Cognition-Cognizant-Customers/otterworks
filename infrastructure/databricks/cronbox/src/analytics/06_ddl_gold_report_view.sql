-- cron-analytics DDL: the legacy daily report as a target-side object.
--
-- Legacy wrote reports/analytics/daily/<ds>/report.json to S3. The replacement
-- keeps the report a derived view over the gold tables instead of a second copy
-- of the numbers, so the parent's live recon can read the report content back
-- out of the platform rather than reassembling it client side.
--
-- peak_hour reproduces `max(hourly_breakdown.items(), key=sum)` over an
-- hour-ascending dict: highest event count, lowest hour on a tie.
-- most_active_users reproduces `[u["user_id"] for u in user_summaries[:5]]`.
-- generated_at is deliberately absent: it is a declared coverage gap
-- (report_generated_at_wall_clock) because the legacy value comes from
-- datetime.now() and is not reconcilable.
CREATE OR REPLACE VIEW ow_tp.gold.analytics_daily_report AS
WITH hourly AS (
  SELECT report_date, namespace, event_hour, SUM(event_count) AS hour_events
  FROM ow_tp.gold.analytics_hourly_breakdown
  GROUP BY report_date, namespace, event_hour
),
peak AS (
  SELECT report_date, namespace, event_hour, hour_events
  FROM hourly
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY report_date, namespace
    ORDER BY hour_events DESC, event_hour ASC
  ) = 1
),
top_users AS (
  SELECT
    report_date,
    namespace,
    TRANSFORM(
      SLICE(SORT_ARRAY(COLLECT_LIST(STRUCT(user_rank, user_id))), 1, 5),
      ranked -> ranked.user_id
    ) AS most_active_users
  FROM ow_tp.gold.analytics_daily_top_users
  GROUP BY report_date, namespace
)
SELECT
  s.report_date,
  s.namespace,
  TO_JSON(STRUCT(
    'daily_analytics' AS report_type,
    CAST(s.report_date AS STRING) AS report_date,
    STRUCT(
      s.active_users AS active_users,
      s.active_documents AS active_documents,
      s.active_files AS active_files,
      s.total_events AS total_events,
      s.documents_created AS documents_created,
      s.documents_edited AS documents_edited,
      s.comments_added AS comments_added,
      s.files_uploaded AS files_uploaded,
      s.files_shared AS files_shared,
      s.files_deleted AS files_deleted,
      s.bytes_uploaded AS bytes_uploaded
    ) AS summary,
    STRUCT(
      STRUCT(p.event_hour AS hour, p.hour_events AS event_count) AS peak_hour,
      t.most_active_users AS most_active_users
    ) AS highlights,
    STRUCT(
      s.documents_created AS created,
      s.documents_edited AS edited,
      s.comments_added AS comments
    ) AS document_metrics,
    STRUCT(
      s.files_uploaded AS uploaded,
      s.files_shared AS shared,
      s.files_deleted AS deleted,
      s.bytes_uploaded AS bytes_uploaded
    ) AS file_metrics
  )) AS report_json
FROM ow_tp.gold.analytics_daily_summary s
JOIN peak p ON p.report_date = s.report_date AND p.namespace = s.namespace
JOIN top_users t ON t.report_date = s.report_date AND t.namespace = s.namespace;
