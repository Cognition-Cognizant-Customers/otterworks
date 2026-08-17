-- cron-analytics gold top users, step 2 of 2: publish the run-date slice.
-- Replaces analytics/daily/**/top_users.jsonl.gz.
--
-- Legacy ordering is `user_summaries.sort(key=total, reverse=True)[:100]` over a
-- dict built in event order, i.e. a stable sort where ties keep first-appearance
-- order. first_seq is the extraction sequence of the user's first event and
-- reproduces exactly that tie-break; action_counts is a MAP, so the legacy
-- artifact's key order carries no meaning and comparison is order-insensitive.
INSERT INTO ow_tp.gold.analytics_daily_top_users
SELECT
  report_date,
  namespace,
  user_id,
  action_counts,
  total_actions,
  CAST(ROW_NUMBER() OVER (ORDER BY total_actions DESC, first_seq ASC) AS INT) AS user_rank,
  first_seq,
  current_timestamp() AS updated_at
FROM (
  SELECT
    report_date,
    namespace,
    resolved_user_id AS user_id,
    MAP_FROM_ENTRIES(COLLECT_LIST(STRUCT(event_type, event_count))) AS action_counts,
    SUM(event_count) AS total_actions,
    MIN(first_seq) AS first_seq
  FROM (
    SELECT
      report_date,
      namespace,
      resolved_user_id,
      event_type,
      COUNT(*) AS event_count,
      MIN(source_seq) AS first_seq
    FROM ow_tp.silver.cronbox_events
    WHERE namespace = :ns AND report_date = :ds
    GROUP BY report_date, namespace, resolved_user_id, event_type
  )
  GROUP BY report_date, namespace, resolved_user_id
)
QUALIFY user_rank <= 100;
