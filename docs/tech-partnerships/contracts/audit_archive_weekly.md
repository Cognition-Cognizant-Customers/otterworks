# Contract: `audit_archive_weekly.py` → `ow_tp_audit_archive`

Read [README.md](README.md) and [_python_wave_baseline.md](_python_wave_baseline.md) first.

| | |
|---|---|
| Source | `etl/scripts/audit_archive_weekly.py` |
| Language / vintage | Python 2014-vintage (boto3 + DynamoDB scan) |
| Legacy schedule | Sunday 03:00 UTC |
| Converted job | `ow_tp_audit_archive` (`infrastructure/terraform-databricks/jobs_audit_archive.tf`) |

## Deficiencies this conversion must retire

Full-table DynamoDB scan to find events older than 90 days (→ predicate pushdown against
the lakehouse, not a scan of everything); batch-delete of source rows with no verified
durable copy first (the archive upload and the delete are not atomic — a failed upload
still deletes); hardcoded credentials; `print()` logging; silent `except: pass`; no
retention policy expressed anywhere but in the code; no idempotency; no alerting.

## Target

| Object | Contents |
|---|---|
| `ow_tp.bronze.audit_events_raw` | source audit/metadata events: `ns`, `event_id`, `event_ts`, `actor`, `action`, `target_id`, `raw_payload`, `ingested_at` |
| `ow_tp.silver.audit_events_archived` | events past the retention horizon, typed, one row per `event_id`, plus `archived_at` and `retention_days` |
| `ow_tp.gold.audit_archive_manifest` | per run: `ns`, `run_date`, `cutoff_ts`, `candidate_count`, `archived_count`, `deleted_count`, `verified BOOLEAN`. `deleted_count > 0` is only permitted when `verified` is true — the archive-then-verify-then-delete ordering the legacy job lacks. |

## Baseline

Legacy run on this VM **succeeded but was vacuous**
(`/home/ubuntu/tp-golden/python/audit_archive_weekly/`, exit code 0):

```text
Found 0 audit events older than 90 days
No events to archive, exiting
```

No archive artifact was produced, so "matches legacy" is trivially true and proves
nothing. To get a meaningful baseline, seed events **older than the 90-day cutoff** into
the source store (the seed anchor is `2026-08-01T00:00:00Z`; derive the cutoff from it, not
from wall-clock time) and re-run the legacy script to capture a real archive artifact.
Record exactly how you aged the data. If you cannot produce a non-empty legacy archive,
fall back to the seed-manifest tier and say so — do **not** report green off the empty run.

## Acceptance checks (`scripts/tp_databricks/recon_audit_archive.py`)

1. Selection parity: the set of `event_id`s in `silver.audit_events_archived` equals the
   set the baseline archived — same cutoff semantics, same boundary handling (be explicit
   about whether the cutoff is inclusive and match the legacy behaviour).
2. Count parity: `candidate_count`, `archived_count` and the archived row count agree, and
   the row count equals the baseline's.
3. Retention safety: no row is marked deleted without `verified = true`, and every archived
   event is still readable from the archive target afterwards. Prove it with a query, not
   a claim.
4. Re-run; assert the second run archives zero additional events and does not
   double-archive (idempotency).
5. Report states the baseline tier verbatim, including how the aged events were produced.
