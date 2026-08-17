CREATE TABLE IF NOT EXISTS ow_tp.gold.user_activity_window_coverage (
  report_date DATE, namespace STRING, window_date DATE, in_summary_window BOOLEAN,
  in_history_window BOOLEAN, summary_present BOOLEAN, history_present BOOLEAN,
  history_user_rows BIGINT, gap_reason STRING, updated_at TIMESTAMP) USING DELTA;
