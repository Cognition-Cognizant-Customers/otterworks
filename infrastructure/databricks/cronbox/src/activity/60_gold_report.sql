DELETE FROM ow_tp.gold.user_activity_daily WHERE namespace = :ns AND report_date = :ds;
INSERT INTO ow_tp.gold.user_activity_daily
WITH summary AS (
  SELECT * FROM ow_tp.gold.analytics_daily_summary
  WHERE namespace = :ns AND report_date BETWEEN DATE_SUB(DATE(:ds), 30) AND DATE(:ds)
), users AS (
  SELECT * FROM ow_tp.gold.user_activity_user_summaries
  WHERE namespace = :ns AND report_date = DATE(:ds)
), coverage AS (
  SELECT * FROM ow_tp.gold.user_activity_window_coverage
  WHERE namespace = :ns AND report_date = DATE(:ds)
), agg AS (
  SELECT COALESCE(SUM(total_events), 0) total_events, COALESCE(MAX(active_users), 0) peak_active_users,
         COUNT(*) reporting_days, COLLECT_LIST(STRUCT(CAST(report_date AS STRING) report_date,
         active_users, active_documents, active_files, total_events, documents_created,
         documents_edited, comments_added, files_uploaded, files_shared, files_deleted, bytes_uploaded)) daily
  FROM summary
), ua AS (
  SELECT COALESCE(COUNT(*), 0) user_summary_count,
         ARRAY_SORT(COLLECT_LIST(STRUCT(user_ordinal, user_id, total_actions, active_days, actions_by_type)),
           (l, r) -> CASE WHEN l.user_ordinal < r.user_ordinal THEN -1
                          WHEN l.user_ordinal > r.user_ordinal THEN 1 ELSE 0 END) rows
  FROM users
), gaps AS (
  SELECT COALESCE(SUM(CASE WHEN history_present THEN 1 ELSE 0 END), 0) history_days_present,
         ARRAY_SORT(COLLECT_LIST(CASE WHEN gap_reason = 'missing_history_partition' THEN CAST(window_date AS STRING) END)) missing
  FROM coverage WHERE in_history_window
)
SELECT DATE(:ds), :ns, 30, DATE_SUB(DATE(:ds), 30), DATE(:ds),
       a.total_events, a.peak_active_users,
       CASE WHEN a.reporting_days = 0 THEN CAST(0 AS DOUBLE) ELSE ROUND(a.total_events / a.reporting_days, 2) END,
       a.reporting_days, u.user_summary_count, LEAST(u.user_summary_count, 20), 30, g.history_days_present,
       FILTER(g.missing, x -> x IS NOT NULL),
       TO_JSON(STRUCT('user_activity' report_type, CAST(DATE(:ds) AS STRING) report_date, 30 lookback_days,
         STRUCT(a.total_events total_events, a.peak_active_users peak_active_users,
                CASE WHEN a.reporting_days = 0 THEN CAST(0 AS DOUBLE) ELSE ROUND(a.total_events / a.reporting_days, 2) END avg_daily_events,
                a.reporting_days reporting_days) trends,
         ARRAY_SORT(a.daily, (l, r) -> CASE WHEN l.report_date < r.report_date THEN -1
                                            WHEN l.report_date > r.report_date THEN 1 ELSE 0 END) daily_summaries,
         TRANSFORM(u.rows, x -> STRUCT(x.user_id, x.total_actions, x.active_days, x.actions_by_type)) user_summaries,
         TRANSFORM(SLICE(u.rows, 1, 20), x -> STRUCT(x.user_id, x.total_actions, x.active_days, x.actions_by_type)) top_users)),
       current_timestamp()
FROM agg a CROSS JOIN ua u CROSS JOIN gaps g;
