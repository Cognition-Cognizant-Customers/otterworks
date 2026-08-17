DELETE FROM ow_tp.gold.user_activity_user_summaries
WHERE namespace = :ns AND report_date = :ds;
