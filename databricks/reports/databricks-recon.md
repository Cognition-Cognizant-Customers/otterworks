# Databricks lakehouse reconciliation report

- Namespace: `dev`
- Phases: custbill, python
- Overall: **PASS**

| Phase | Check | Status | Detail |
|---|---|---|---|
| custbill | silver_rows_match_legacy_psv | PASS | (100 legacy rows vs 100 silver rows) |
| custbill | gold_matches_legacy_finance_report | PASS | (6 aggregate rows) |
| custbill | clean_input_zero_quarantine | PASS |  |
| custbill | trailer_counts_reconciled | PASS |  |
| python | analytics_daily_summary | PASS | (3 days) |
| python | analytics_event_type_daily | PASS | (30 day×type rows) |
| python | audit_archive_counts | PASS |  |
| python | storage_cleanup_report | PASS | (40 planted dangling refs) |
| python | search_index_counts | PASS |  |
| python | user_activity_totals | PASS | (50 users) |
