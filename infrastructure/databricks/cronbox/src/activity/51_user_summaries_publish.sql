INSERT INTO ow_tp.gold.user_activity_user_summaries
WITH win AS (
  SELECT namespace, COALESCE(user_id, 'unknown') AS user_id, report_date,
         total_actions, action_counts, first_seq
  FROM ow_tp.gold.analytics_daily_top_users
  WHERE namespace = :ns
    AND report_date BETWEEN DATE_SUB(DATE(:ds), 29) AND DATE(:ds)
), per_user_day AS (
  SELECT namespace, user_id, report_date, SUM(total_actions) AS day_total,
         COUNT(*) AS day_records, MIN(first_seq) AS day_seq
  FROM win
  GROUP BY namespace, user_id, report_date
), totals AS (
  SELECT namespace, user_id, SUM(day_total) AS total_actions,
         SUM(day_records) AS active_days
  FROM per_user_day
  GROUP BY namespace, user_id
), firsts AS (
  SELECT namespace, user_id, report_date AS first_seen_date, day_seq AS first_seen_seq
  FROM per_user_day
  QUALIFY ROW_NUMBER() OVER (PARTITION BY namespace, user_id ORDER BY report_date DESC) = 1
), action_totals AS (
  SELECT namespace, user_id, action_key, SUM(action_value) AS action_value
  FROM win LATERAL VIEW EXPLODE(action_counts) e AS action_key, action_value
  GROUP BY namespace, user_id, action_key
), actions AS (
  SELECT namespace, user_id,
         MAP_FROM_ENTRIES(ARRAY_SORT(COLLECT_LIST(STRUCT(action_key, action_value)))) AS actions_by_type
  FROM action_totals
  GROUP BY namespace, user_id
), ranked AS (
  SELECT t.namespace, t.user_id, t.total_actions, CAST(t.active_days AS INT) AS active_days,
         COALESCE(a.actions_by_type, CAST(map() AS MAP<STRING, BIGINT>)) AS actions_by_type,
         f.first_seen_date, f.first_seen_seq,
         CAST(ROW_NUMBER() OVER (
           ORDER BY t.total_actions DESC, f.first_seen_date DESC,
                    COALESCE(f.first_seen_seq, 9223372036854775807) ASC
         ) AS INT) AS user_ordinal
  FROM totals t
  JOIN firsts f ON t.namespace = f.namespace AND t.user_id = f.user_id
  LEFT JOIN actions a ON t.namespace = a.namespace AND t.user_id = a.user_id
)
SELECT DATE(:ds), namespace, user_id, total_actions, active_days, actions_by_type,
       user_ordinal, user_ordinal <= 20, first_seen_date, first_seen_seq, current_timestamp()
FROM ranked
WHERE user_ordinal <= 500;
