# Cron Box modernization rollup — `NS=demo`

One legacy cron server, five Python jobs (`etl/scripts/`, 1,467 LOC), retired by routing each job
to the platform that owns the capability — not by treating the estate as five identical migrations.

Run branch: `tp-run/modernize-20260817T043437Z` · before-state branch: `tech-partnerships` (never a target).

## Routing and result

| Legacy job | LOC deleted | Disposition | Platform | Target | Live recon | Child session |
|---|---|---|---|---|---|---|
| `analytics_daily.py` | 452 | rewrite | Databricks | `ow_tp.bronze/silver/gold` + `ow_tp_cron_analytics_daily` | GREEN — 32 checks, 0 failed, 1 declared skip | [session](https://partner-workshops.devinenterprise.com/sessions/f128059328e344228534dd87310034ee) · [#972](https://github.com/Cognition-Partner-Workshops/otterworks/pull/972) |
| `user_activity_daily.py` | 255 | rewrite | Databricks | `ow_tp.gold.user_activity_*` + `ow_tp_cron_user_activity_daily` | GREEN — 28 checks, 0 failed, 1 declared skip | [session](https://partner-workshops.devinenterprise.com/sessions/ce3e02ae3cfb41dba4a30cbd72cd04a6) · [#983](https://github.com/Cognition-Partner-Workshops/otterworks/pull/983) |
| `audit_archive_weekly.py` | 224 | replace-with-config | AWS | DynamoDB TTL → Streams → Lambda → S3 Glacier lifecycle | GREEN — 19 checks, 0 failed, 2 declared skips | [session](https://partner-workshops.devinenterprise.com/sessions/ecde4a1393ba4e50be432fa5c71b5afa) · [#969](https://github.com/Cognition-Partner-Workshops/otterworks/pull/969) |
| `storage_cleanup_daily.py` | 217 | replace-with-config | AWS | S3 EventBridge → Lambda → SQS quarantine + lifecycle expiry | GREEN — 36 checks, 0 failed | [session](https://partner-workshops.devinenterprise.com/sessions/4f9a9f2520f34790bc6f2136836d6029) · [#973](https://github.com/Cognition-Partner-Workshops/otterworks/pull/973) |
| `search_reindex_weekly.py` | 319 | delete | MongoDB Atlas | `ow_tp_cronbox_demo.{documents,files}` Atlas Search indexes (continuous) | GREEN — 16 checks, 0 failed | [session](https://partner-workshops.devinenterprise.com/sessions/535b55447b95410eb7b8daca6e78a829) · [#970](https://github.com/Cognition-Partner-Workshops/otterworks/pull/970), [#980](https://github.com/Cognition-Partner-Workshops/otterworks/pull/980) |
| **total** | **1,467** | 2 rewrite / 2 replace-with-config / 1 delete | 3 platforms | — | 5/5 GREEN | — |

Also deleted: `etl/crontab` (7 lines) and `etl/config.ini` (25 lines, plaintext credentials).

## Recon evidence

All five reports are schema-valid (`make tp-validate-recon`), `run_mode: "live"`,
`values_recomputed_from_target: true`, with an idempotency rerun observed and planted anomalies
compared as sets (`missing: []`, `unexpected: []` in every unit). Reports:
`docs/tech-partnerships/recon/cron-{analytics,activity,archive,cleanup,search}-demo.recon.json`.

Every number was recomputed from the deployed platform in a single uncontended window — the
warehouse for Databricks units, the AWS APIs (never tfstate) for the config units, and `$search`
against Atlas — never from the migration code's own output.

Selected live observations:

- **analytics**: 272 events reconciled from the warehouse (240 SQS + 32 run-date DynamoDB), 12 active
  users / 25 documents / 9 files, 8 malformed bodies and 22 `unknown`-actor events attributed as the
  legacy job attributed them.
- **activity**: 30-day window including the run date, the planted missing history day (`2026-01-02`)
  detected as a coverage gap that contributes nothing, dated report and `latest` pointer identical by
  `report_sha256`, run-date analytics row untouched by the backfill (`report_date < ds` guard).
- **archive**: exact `81 archived / 22 retained` split with exclusive-cutoff boundary behavior
  (`boundary-0` archived, `boundary-1`/`boundary-2` retained), TTL/Streams/lifecycle read back from the
  API. Skips are declared: the bounded live TTL-removal probe (DynamoDB's own sweeper is best-effort
  within ~48h) and Glacier storage-class observation.
- **cleanup**: 4 object-only orphans quarantined through the event-driven path and 1 reverse metadata
  orphan recorded, on-demand billing everywhere, EventBridge rule with no schedule.
- **search**: the full golden query set answered by Atlas `$search` with the legacy counts
  (125 documents, 72 files; unicode `Δocument`/`Fichier` = 1 each; `tag-2` = 25; `folder-2` = 24;
  `application/pdf` = 18) — continuous indexing, so the weekly reindex has no successor to run.

## Cost and teardown posture

Zero hourly-cost resources: Databricks serverless SQL only with every schedule created `PAUSED`;
AWS is Lambda/SQS/EventBridge/DynamoDB on-demand/S3 only, all `ow-tp-` prefixed and tagged
`Project=otterworks-tp` so teardown is provable by scan; Atlas is the free-tier M0. Nothing is
scheduled between demos — the AWS event rules exist only while the stack is applied, and the
persistent `ow_tp` tables and Atlas collections/search indexes are browsable but inert.
