CREATE TABLE IF NOT EXISTS ow_tp.gold.user_activity_user_summaries (
  report_date DATE, namespace STRING, user_id STRING, total_actions BIGINT, active_days INT,
  actions_by_type MAP<STRING,BIGINT>, user_ordinal INT, is_top_user BOOLEAN,
  first_seen_date DATE, first_seen_seq BIGINT, updated_at TIMESTAMP) USING DELTA;
