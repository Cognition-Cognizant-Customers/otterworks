DELETE FROM ow_tp.gold.user_activity_window_coverage
WHERE namespace = :ns AND report_date = :ds;
INSERT INTO ow_tp.gold.user_activity_window_coverage
WITH days AS (
    SELECT EXPLODE(SEQUENCE(DATE_SUB(DATE(:ds), 30), DATE(:ds), INTERVAL 1 DAY)) AS window_date
), counts AS (
  SELECT d.window_date,
    s.report_date IS NOT NULL AS summary_present,
    h.report_date IS NOT NULL AS history_present,
    COALESCE(h.user_rows, 0) AS history_user_rows
  FROM days d
  LEFT JOIN (SELECT report_date FROM ow_tp.gold.analytics_daily_summary WHERE namespace = :ns GROUP BY report_date) s
    ON d.window_date = s.report_date
  LEFT JOIN (
    SELECT report_date, COUNT(*) user_rows
    FROM ow_tp.gold.analytics_daily_top_users
    WHERE namespace = :ns AND report_date BETWEEN DATE_SUB(DATE(:ds), 29) AND DATE(:ds)
    GROUP BY report_date
  ) h ON d.window_date = h.report_date
)
SELECT DATE(:ds), :ns, window_date, TRUE, window_date >= DATE_SUB(DATE(:ds), 29),
       summary_present, history_present, history_user_rows,
       CASE WHEN window_date >= DATE_SUB(DATE(:ds), 29) AND NOT history_present THEN 'missing_history_partition'
            WHEN NOT summary_present THEN 'summary_row_absent' END, current_timestamp()
FROM counts;
