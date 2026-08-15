# Contract: `storage_cleanup_daily.py` → `ow_tp_storage_cleanup`

Read [README.md](README.md) and [_python_wave_baseline.md](_python_wave_baseline.md) first.

| | |
|---|---|
| Source | `etl/scripts/storage_cleanup_daily.py` |
| Language / vintage | Python 2014-vintage (boto3 S3 + DynamoDB) |
| Legacy schedule | daily 02:30 UTC |
| Converted job | `ow_tp_storage_cleanup` (`infrastructure/terraform-databricks/jobs_storage_cleanup.tf`) |

## Deficiencies this conversion must retire

Orphan detection by listing every S3 object and checking each against DynamoDB one item at
a time (→ set difference in SQL); **destructive action driven by that comparison with no
dry-run and no guard** — a metadata read failure looks exactly like "this file is an
orphan", so a transient DynamoDB problem quarantines live files; hardcoded credentials;
`print()` logging; silent `except: pass`; savings report written nowhere durable; no
idempotency; no alerting.

## Target

| Object | Contents |
|---|---|
| `ow_tp.bronze.storage_objects_raw` | object inventory: `ns`, `bucket`, `key`, `size_bytes`, `last_modified`, `listed_at` |
| `ow_tp.bronze.file_metadata_raw` | file-metadata items: `ns`, `file_id`, `storage_key`, `owner_id`, `size_bytes`, `created_at` |
| `ow_tp.silver.storage_orphans` | objects with no metadata row: `ns`, `bucket`, `key`, `size_bytes`, `orphan_reason`, `detected_at`, `metadata_read_ok BOOLEAN` — an orphan verdict is only valid when the metadata side was read successfully and completely |
| `ow_tp.gold.storage_cleanup_savings` | per run: `ns`, `run_date`, `objects_scanned`, `metadata_rows`, `orphan_count`, `orphan_bytes`, `quarantined_count`, `dry_run BOOLEAN` |

Guard requirement: if the metadata read is incomplete, the job records the orphan
candidates and **quarantines nothing**. Encode this as an explicit condition, not a comment.

## Baseline

Legacy run on this VM **failed** (`/home/ubuntu/tp-golden/python/storage_cleanup_daily/`):

```text
Listing objects in s3://otterworks-file-storage/files/
FATAL: An error occurred (NoSuchBucket) when calling the ListObjectsV2 operation: The specified bucket does not exist
```

Tier 1 attempt: create `otterworks-file-storage` in LocalStack and populate
`files/` from the seeded DynamoDB `otterworks-file-metadata` items (10,000 items for
`ns=demo`), deliberately leaving a known, recorded set of objects with no metadata row so
there is a non-trivial orphan set to reconcile. Record the exact orphan set you planted —
it is the expected answer. Then run the legacy script for the golden report. If the bucket
layout cannot be made to satisfy the script without editing it, fall back to the
seed-manifest tier and say so.

## Acceptance checks (`scripts/tp_databricks/recon_storage_cleanup.py`)

1. Orphan-set parity: the `(bucket, key)` set in `silver.storage_orphans` equals the
   baseline orphan set exactly — no extras (a false positive here is a deleted customer
   file) and none missing.
2. `orphan_bytes` equals the summed object sizes of that set exactly, and
   `objects_scanned` / `metadata_rows` match the baseline counts.
3. Safety guard: simulate an incomplete metadata read and show the run quarantines nothing
   and does not report those objects as confirmed orphans. This is the headline deficiency —
   recon is not green without this demonstration.
4. Re-run; assert the orphan set and totals are unchanged (idempotency).
5. Report states the baseline tier verbatim and lists the planted orphan set.
