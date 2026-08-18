# MongoDB data-migration wave rollup — NS=demo (run branch tp-run/mongodb-20260817T233337Z)

Live validation window against Atlas cluster `otterworks-demo`, databases
`ow_tp_mongodb_demo` / `ow_tp_mongodb_demo_quarantine`. All values recomputed
from the target after migration; idempotency proven by an actual rerun
(collection counts and checksums identical before/after). Recon reports are
schema-gated (`make tp-validate-recon`) and committed alongside this file.

| Workload | Documents migrated | Checksum match | Planted anomalies detected | Child session |
|---|---|---|---|---|
| customers | 25,000 (EAV folded: 8,333; quarantined: 81) | match (`4f92feef2ad58dbab30e289957931928`) | 2/2 exact set (malformed_csv_lists:31, dirty_dates:50) | [session](https://partner-workshops.devinenterprise.com/sessions/60ec93d12c8441899e524fa4e13d4a1e) — [PR #1162](https://github.com/Cognition-Partner-Workshops/otterworks/pull/1162) |
| invoices | 18,750 (embedded lines: 149,963; quarantined orphans: 37) | match (`88a66751f0b08b476b492105a2efc537`, every source line accounted once) | 1/1 exact set (orphaned_rows:37) | [session](https://partner-workshops.devinenterprise.com/sessions/7f698e069bbf431a92cae0510b65448b) — [PR #1164](https://github.com/Cognition-Partner-Workshops/otterworks/pull/1164) |
| documents | 2,000 (embedded versions: 13,876; snapshots: 384 embedded + 6 quarantined) | match (all three: docs `e70001cf…`, versions `13bc033b…`, snapshots `abe69084…`) | 2/2 exact set (version_gaps:10, orphaned_snapshots:6) | [session](https://partner-workshops.devinenterprise.com/sessions/171410ba3e6d49a5a79acf6e389feb53) — [PR #1163](https://github.com/Cognition-Partner-Workshops/otterworks/pull/1163) |
| files | 10,000 (quarantined orphaned metadata: 40) | match (`db614663b2c6d41141cae82261b416d5`) | 1/1 exact set (orphaned_metadata:40); missing_hours:1 is a declared contractual coverage_gap (S3 events prefix not ingested by this track) | [session](https://partner-workshops.devinenterprise.com/sessions/539d2ea8e32d47aaaa7e59052775c638) — [PR #1166](https://github.com/Cognition-Partner-Workshops/otterworks/pull/1166) |

Run-level anomaly attribution: 7 planted anomaly kinds in the baseline manifest;
6 detected exactly by units, 1 (`missing_hours`) declared as a coverage gap in
`mongo_files.json`. No missing, no unexpected detections in any unit.

NS=demo is left up and browsable on Atlas per run policy.

## Stored-procs wave (!tp_mongo_3_procs) — rating + invoicing extraction

Legacy billing stored procedures extracted into `services/billing-service/`
reading the migrated MongoDB collections only (hard cutover — no PostgreSQL
path for extracted modules). Zero re-recorded transcripts; CI/local runs use
the deterministic compose Mongo fixture (no Atlas dependency).

| Unit | PR | Result |
|---|---|---|
| Compose Mongo document-store fixture | [PR #1172](https://github.com/Cognition-Partner-Workshops/otterworks/pull/1172) | merged; `make up`/`make procs-up` self-contained |
| rating (8 scenarios) | [PR #1174](https://github.com/Cognition-Partner-Workshops/otterworks/pull/1174) | merged; rules gate PASS, parity PASS=8 FAIL=0 |
| invoicing (6 scenarios, embedded lines) | [PR #1177](https://github.com/Cognition-Partner-Workshops/otterworks/pull/1177) | merged; rules gate PASS, parity PASS=6 FAIL=0 |

Parent verification on the run branch: `make procs-parity NS=demo` →
`Parity PASS=19 FAIL=0 SKIP=5` (plans + rating + invoicing graded PASS, dunning
SKIP), `make procs-rules-gate ALL=1` PASS (invoicing, plans, rating),
`make tp-smoke` green.

Live-window check (parent, uncontended): billing-service pointed at Atlas
database `ow_tp_mongodb_demo` (NS=demo) and the full parity suite replayed once
against live data → `Parity PASS=19 FAIL=0 SKIP=5`. The parity fixture slice
(`origin: billing_svc`) written during the run was removed afterwards; migrated
document counts verified unchanged (customers 25,000 / invoices 18,750).
