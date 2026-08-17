CREATE TABLE IF NOT EXISTS ow_tp.gold.user_activity_daily (
  report_date DATE, namespace STRING, lookback_days INT, window_start DATE, window_end DATE,
  total_events BIGINT, peak_active_users INT, avg_daily_events DOUBLE, reporting_days INT,
  user_summary_count INT, top_user_count INT, history_days_expected INT,
  history_days_present INT, missing_history_days ARRAY<STRING>, report_json STRING,
  updated_at TIMESTAMP) USING DELTA;
