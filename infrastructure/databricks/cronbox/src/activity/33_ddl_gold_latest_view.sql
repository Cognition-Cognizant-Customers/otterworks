CREATE OR REPLACE VIEW ow_tp.gold.user_activity_latest AS
SELECT * FROM ow_tp.gold.user_activity_daily
QUALIFY ROW_NUMBER() OVER (PARTITION BY namespace ORDER BY report_date DESC) = 1;
