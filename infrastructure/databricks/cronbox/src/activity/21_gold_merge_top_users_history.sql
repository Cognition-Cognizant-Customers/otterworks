MERGE INTO ow_tp.gold.analytics_daily_top_users t
USING (
  SELECT namespace, report_date, user_id, total AS total_actions,
         from_json(actions_json, 'MAP<STRING,BIGINT>') AS action_counts,
         CAST(ROW_NUMBER() OVER (PARTITION BY report_date ORDER BY total DESC, source_line ASC) AS INT) AS user_rank,
         source_line AS first_seq
  FROM ow_tp.bronze.cronbox_activity_history_top_users
  WHERE namespace = :ns AND report_date < DATE(:ds)
    AND parse_error IS NULL
) s ON t.namespace = s.namespace AND t.report_date = s.report_date AND t.user_id = s.user_id
WHEN MATCHED THEN UPDATE SET
  t.total_actions = s.total_actions, t.action_counts = s.action_counts,
  t.user_rank = s.user_rank, t.first_seq = s.first_seq, t.updated_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  namespace, report_date, user_id, total_actions, action_counts, user_rank, first_seq, updated_at
) VALUES (
  s.namespace, s.report_date, s.user_id, s.total_actions, s.action_counts,
  s.user_rank, s.first_seq, current_timestamp()
);
