# Recon: `ow_tp_estate_orchestrator` + `ow_tp.gold.estate_daily_rollup` vs `etl/legacy-extra/run_all.sh`

- verdict: **green** — 5/5 acceptance checks, `ns=demo`, `run_date=2026-08-16`
- recon tool: `scripts/tp_databricks/recon_estate_rollup.py` (every number below is that script's
  own output; nothing here is hand-entered)
- legacy baseline for the CUSTBILL cross-foot:
  `/home/ubuntu/tp-golden/custbill/reports/finance_billing_20260816.csv`
  (sha256 `c8923a71ab5a2d8048ad06ae91840631c009551e9082755fa4672e034a15627e`), produced on this
  machine by the legacy Perl job via `make legacy-etl-run`, not by the conversion
- failure-drill artifact: `/home/ubuntu/tp-golden/estate/faildrill-2026-08-18.json`
- seed manifest: `testdata/legacy/manifests/demo.json`, sha256
  `879bc13782c937b85352068988d95a535a3ac509ec7b03b90f9565647783c402` (runtime state, not committed;
  landed into `ow_tp.bronze.seed_anomaly_manifest` so anomaly rows can cite it)

Reproduce with:

```bash
export DATABRICKS_HOST="${DATABRICKS_DEMO_HOST%/}" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
python3 scripts/tp_databricks/run_estate_rollup.py ddl
python3 scripts/tp_databricks/run_estate_rollup.py manifest --ns demo
python3 scripts/tp_databricks/run_estate_rollup.py run --ns demo --run-date 2026-08-16
python3 scripts/tp_databricks/recon_estate_rollup.py --ns demo --run-date 2026-08-16
```

## What the legacy estate did, and what replaces it

`etl/legacy-extra/run_all.sh` ordered eight jobs by wall clock (`sleep 300` between phases),
overlapped with its own crontab entries, discarded every job's stderr (`2>/dev/null`) and ended
`|| true`, so the wrapper exited 0 whether or not the estate ran. Nothing above the individual
scripts ever reconciled the night's output, and the seeded data defects nobody looked at stayed
invisible.

The replacement is `ow_tp_estate_orchestrator`: ordering is `depends_on` DAG edges over
`run_job_task` calls to the eight converted unit jobs, one run at a time, ending in
`ow_tp_estate_rollup`, which writes one reconciled row per unit and the anomalies, then **fails**
if any unit is not green.

## Check 1 — exactly one row per converted unit, `recon_result` derived

8 of 8 units, one row each for `(ns='demo', run_date='2026-08-16')`. The recon script recomputes
each verdict from that unit's own evidence tables and compares it with the stored value, so a
hand-entered, stale or copied verdict fails the check. All eight agreed:

| unit | recon_result (stored / recomputed) | rows_in | rows_out | rejected | identity that had to hold |
|---|---|---:|---:|---:|---|
| `sftp_ingest` | green / green | 2 files | 2 | 0 | `files_reconciled = files_landed`; 2=2 (100 trailer-declared records) |
| `parse_custbill` | green / green | 100 records | 100 | 0 | `parsed + rejected = trailer_declared`; 100+0=100 |
| `finance_report` | green / green | 100 records | 6 groups | 0 | `sum(record_count)=parsed` and `sum(total_amount)=sum(silver.amount)`; 100=100, 510391.14=510391.14 |
| `analytics_daily` | green / green | 5147 events | 5147 | 0 | `gold_event_count + rejected = silver`; 5147+0=5147 |
| `audit_archive` | green / green | 4091 events | 4091 | 0 | `archived = candidates` and `silver_rows = archived`; 4091=4091 |
| `search_reindex` | green / green | 11394 documents | 11394 | 0 | `indexed = extracted` and `silver_rows = indexed`; 11394=11394 |
| `storage_cleanup` | green / green | 9985 objects | 25 | 0 | `silver_orphan_rows = gold_orphan_count`; 25=25, `metadata_read_ok=true` |
| `user_activity` | green / green | 5147 events | 5147 | 0 | `gold_report_events = silver_events`; 5147=5147 (50 report rows) |

`rows_in` is not the same unit of measure across units — files for ingest, objects for cleanup,
records or events elsewhere — so each row names its own unit of measure in `recon_detail`, together
with the tables the numbers were read from, the slice date, and that unit's standing disclosure
(`finance_report`'s `delivery_status=NOT_DELIVERED_NO_TRANSPORT_CONFIGURED`, `storage_cleanup`'s
`dry_run=true`, the weekly units whose slice legitimately predates the run date).

The units do not persist history the same way: `finance_billing_summary`, `search_reindex_summary`,
`user_activity_report` and `audit_archive_manifest` keep one slice per business date and add to it,
while the silver tables they are reconciled against are replaced per namespace on every run. Each
of those gold measures is therefore scoped to the unit's own latest slice, so both sides of an
identity describe the same run and a unit stays green on its second night instead of comparing
accumulated history against a single silver slice. `analytics_daily_summary` is deliberately not
scoped — it is written `REPLACE WHERE ns`, so its several `summary_date` rows are all one run.

`job_run_id` is empty for the rows above and the report labels them as such: they were written by
the local warehouse runner, because the unit jobs are not applied in this shared workspace (the
parent session owns `terraform apply`). A job-written slice is shown under check 3.

## Check 2 — CUSTBILL cross-foots to the legacy baseline, to the cent

Legacy Perl report (left) vs `ow_tp.gold.finance_billing_summary` (right), all six
currency/record-type groups equal. The gold table is keyed by `(ns, report_date, currency,
record_type)` and the finance job replaces only its own date slice, so the recon resolves the
business day explicitly and names it rather than comparing one day's legacy report against every
slice in the namespace: `report_date 2026-08-15` is the only slice present for `ns=demo`, and a
second slice makes the check demand `--report-date` instead of picking one.

| currency | type | legacy count / amount | gold count / amount |
|---|---|---:|---:|
| EUR | INVOICE | 22 / 101554.41 | 22 / 101554.41 |
| EUR | CREDIT | 6 / 33375.97 | 6 / 33375.97 |
| GBP | INVOICE | 32 / 183113.58 | 32 / 183113.58 |
| GBP | CREDIT | 5 / 28454.59 | 5 / 28454.59 |
| USD | INVOICE | 28 / 130502.15 | 28 / 130502.15 |
| USD | CREDIT | 7 / 33390.44 | 7 / 33390.44 |

Cross-foot: 100 records / 510391.14 in `silver.custbill_records` (the whole `ns=demo` parse slice —
that table carries no `report_date`) == 100 / 510391.14 summed over the legacy report's own rows.
Amounts are compared as decimals, so a cent of drift fails.

## Check 3 — a real failing upstream, in a real multi-task run

Not a simulation. `scripts/tp_databricks/run_estate_dev.py` created a throwaway serverless
multi-task job that mirrors the shipped DAG (`ow_tp_dev_estate_orchestrator`), pointed
`parse_custbill` at a source table that does not exist, ran it, and deleted the job afterwards:

- run `812883922335192`, `result_state=FAILED`
- `parse_custbill` **FAILED**; `finance_report` and `estate_rollup` **UPSTREAM_FAILED** (skipped)
- rollup rows for the drill's `run_date` `2026-08-18`, asked of the live table by the recon script
  after the fact: **none** — no green row, no row at all
- `job_torn_down: true`; the temporary job and its notebook copy are gone, and nothing under `etl/`
  was modified to stage the failure

The legacy comparison is the point: `run_all.sh` with the same broken input printed nothing and
exited 0.

A second run of the same mirrored DAG with no injected failure (`971716061721650`) is recorded
because it is instructive rather than green: every unit task succeeded, and `estate_rollup` then
**failed the run** because `storage_cleanup` was red at that moment — another session was
concurrently rewriting `ow_tp.gold.storage_cleanup_savings` and `ow_tp.bronze.file_metadata_raw` in
this shared workspace, and the latest persisted storage evidence was its `metadata_read_incomplete`
scenario (`metadata_read_ok=false`). That is the intended behaviour: the verdict follows the
evidence, so an estate that is not reconciled cannot report green. Re-running against a settled
slice produced the 8/8 green above. See the disclosure below.

## Check 4 — seeded anomalies present and traceable

Manifest (`sha256 879bc137…c402`) vs `ow_tp.gold.estate_anomalies` for `ns=demo`, 43 rows:

| manifest kind | planted | target | detected | via |
|---|---:|---|---:|---|
| `orphaned_metadata` | 40 | DynamoDB `file-metadata` | 40 | `storage_cleanup` (`bronze.file_metadata_raw` keys the seed never wrote) |
| `missing_hours` | 1 | S3 `data-lake/events/demo/` | 1 | `analytics_daily` (the absent hour `2026-07-29T04:00:00Z`) |
| `orphaned_snapshots` | 6 | PostgreSQL `document_snapshots` | coverage gap | recorded as 1 explicit gap row |
| `version_gaps` | 10 | PostgreSQL `document_versions` | coverage gap | recorded as 1 explicit gap row |

Rows with no manifest entry: **0** — every anomaly row carries the identifier plus the manifest
kind, target, planted count, `generated_at` and sha256 it traces to.

Disclosed deficiency, not fixed: no converted unit ingests the PostgreSQL document tables, so the
`orphaned_snapshots` (6) and `version_gaps` (10) kinds cannot be detected from the lakehouse at all.
Rather than let their absence read as "no anomalies", the rollup writes an explicit coverage-gap row
per kind (`unit='seed_manifest'`) naming the planted count and the source that would have to be
converted. The recon check fails if a planted kind is neither detected nor declared as a gap.

## Check 5 — the committed job configuration, parsed

Read out of `infrastructure/terraform-databricks/jobs_estate_rollup.tf` by the recon script:

- jobs defined: `estate_rollup`, `estate_orchestrator`; **both** set `max_concurrent_runs = 1`
  (matched with a digit boundary, so a value like `10` fails the check rather than satisfying it)
  (queueing enabled, so a queued run stays visible instead of being dropped like an overlapping cron
  invocation)
- sleep-based ordering: **none** (no occurrence of `sleep` anywhere in the file)
- cluster configuration: **none** — `new_cluster`, `existing_cluster_id`, `job_cluster` and
  `job_clusters` are all absent; every task is serverless, and the rollup runs on the pre-existing
  `Serverless Starter Warehouse` where it needs SQL
- DAG edges, compared against the contract's required graph:
  `sftp_ingest -> parse_custbill -> finance_report`, `analytics_daily -> user_activity`,
  `audit_archive`, `search_reindex` and `storage_cleanup` independent, and `estate_rollup` depending
  on every leaf (`finance_report`, `user_activity`, `audit_archive`, `search_reindex`,
  `storage_cleanup`) — a dropped edge fails this check instead of surviving as prose here
- the estate schedule replaces the legacy 02:00–05:00 nightly window and ships **paused**, so
  applying the Terraform cannot start unrehearsed estate runs

## Disclosures

1. **The PAT lacks the Databricks `files` scope.** Writes to `/Volumes/ow_tp/bronze/landing` fail
   with `403 … required scopes: files`, exactly as the per-unit waves disclosed. The seed manifest is
   therefore landed into `ow_tp.bronze.seed_anomaly_manifest` over the serverless warehouse instead
   of as a volume file. No credential was invented or requested.
2. **The unit jobs are not applied in this workspace**, so the failure drill could not use
   `run_job_task` edges to them; the mirrored DAG asserts the same thing from the data side (a unit
   that published no evidence fails its task). The shipped orchestrator in
   `jobs_estate_rollup.tf` does use `run_job_task` edges — check 5 verifies that from the committed
   configuration. `terraform apply` was deliberately not run: `fmt`, `validate` and `plan` only.
3. **`terraform plan` cannot complete on this branch for a pre-existing reason.** The plan reaches
   `Plan: 30 to add, 0 to change, 0 to destroy` and then fails in an unmodified file,
   `jobs_finance_report.tf:126`, which passes `depends_on` as an argument where the provider expects
   a block. That file belongs to an earlier wave and was left alone. No cluster resource appears in
   the plan.
4. **This is a shared workspace and other sessions write the same tables.** Two rollup runs during
   this session correctly reported `storage_cleanup` red while another session's storage drill was
   the latest persisted evidence (`scenario=metadata_read_incomplete`, `metadata_read_ok=false`), and
   the anomaly count moved with its rewrites of `bronze.file_metadata_raw` (32 detected mid-rewrite
   vs 40 once settled). The rollup deliberately reads the latest persisted evidence rather than
   selecting the row that would be green, which is why the green slice above is timestamped and
   reproducible rather than absolute.
5. **`job_run_id` is empty for the green slice** because it was written by the local warehouse
   runner rather than by `ow_tp_estate_rollup` — stated on the rows themselves, not just here.
