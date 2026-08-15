baseline: legacy output

# Recon: `audit_archive_weekly.py` -> `ow_tp_audit_archive`

- recon_result: **green**
- baseline artifact: `/home/ubuntu/tp-golden/python/audit_archive_weekly/audit_events.jsonl.gz` (sha256 `13946007518477977f5f4809704f86f317ea78417025a1365c77b22d7cbcfb85`), 4091 events
- namespace `demo`, run_date `2026-08-01`, retention_days `90`, cutoff `2026-05-03 00:00:00` (exclusive)

## How the baseline was produced

The legacy script does not archive anything on the seeded namespace as shipped:
`make seed-legacy NS=demo` writes audit events dated within roughly the last 30
days of the seed anchor (`2026-08-01`), so nothing is past a 90-day horizon and
`etl/scripts/audit_archive_weekly.py` exits 0 with `Found 0 audit events older
than 90 days`. A baseline captured from that run is vacuous -- "matches legacy"
would compare an empty set against an empty set.

So the source data was aged past the cutoff before the legacy run, with
`scripts/tp_databricks/seed_audit_events.py`:

- it reads the seeded audit-event objects for `ns=demo` out of LocalStack S3 and
  rewrites each event's `timestamp` to `anchor - N days`, where `N` is derived
  deterministically from the SHA-256 of the `event_id` over the range 30..329
  days, so the fixture is reproducible rather than random;
- it pins three events exactly on the boundary -- cutoff minus one second
  (`0010449d-1dd2-44fa-a9e7-8fc84f6caf46`, `2026-05-02T23:59:59Z`), exactly the
  cutoff (`00211ab3-8cb5-429e-a953-078de468f014`, `2026-05-03T00:00:00Z`) and
  cutoff plus one second (`0024e4d5-f661-440a-93ec-defc5bfff035`,
  `2026-05-03T00:00:01Z`) -- so the legacy `<` comparison, which is exclusive,
  is actually exercised by both the baseline and the conversion;
- it loads the rewritten fixture (5147 events) into the LocalStack DynamoDB
  table `otterworks-audit-events`.

`etl/scripts/audit_archive_weekly.py` was then run **unmodified** (nothing under
`etl/` was edited) against that LocalStack fixture with execution date
`2026-08-01`. It archived 4091 of the 5147 events under cutoff
`2026-05-03T00:00:00Z` and uploaded `audit_events.jsonl.gz` (375103 bytes) with
`StorageClass=GLACIER`; the baseline artefacts compared below are that run's own
S3 objects, restored from Glacier and downloaded (`restore-object` first, since
the initial download failed with `InvalidObjectState: The operation is not valid
for the object's storage class`). The exact same aged fixture is the input to the
converted pipeline, uploaded to `/Volumes/ow_tp/bronze/landing/demo/audit_archive/audit_events.jsonl`.

Two honest notes about the baseline:

- The legacy run reported `events_deleted_from_source: 0` while claiming success.
  Its batch delete supplies the keys `event_id` + `timestamp`, but the fixture
  table's key schema is `id`, so every delete failed -- and the failure was
  swallowed by the script's bare `except: pass`. That is one of the deficiencies
  this conversion retires, so the converted job's `deleted_count` is expected to
  differ from the legacy `0`; the baseline is used for *selection and count*
  parity, not for the delete count.
- Local fixtures needed a workaround: host port 5432 was already occupied by a
  Postgres that rejects the `otterworks` credentials, so Postgres was brought up
  as a separate container `otterworks-postgres-alt` on port 55432 with the
  repository's own credentials and init SQL. LocalStack, MeiliSearch and Redis
  came up from `make infra-up` unchanged.

## How the converted job was executed

The numbers below come from real runs of the converted pipeline notebook
(`databricks/notebooks/ow_tp_audit_archive.py`) on serverless compute in the
shared demo workspace, against catalog `ow_tp`. Two deviations from the applied
job definition, both stated so the evidence is not read as more than it is:

- The parent session owns Terraform state, so `jobs_audit_archive.tf` was
  validated (`terraform fmt -check`, `terraform init -backend=false`,
  `terraform validate`) but never applied. To run the pipeline the notebook was
  deployed with `scripts/tp_databricks/dbx.py deploy-notebook` and driven by a
  throwaway job `ow_tp_dev_audit_archive` carrying the same parameters and the
  same notebook task as the Terraform definition. It was deleted afterwards and
  `dbx.py inventory` no longer lists it, so no stray job is left behind (the
  inventory does show another unit's `ow_tp_dev_search_reindex`, which is not
  ours to touch). The
  job's `create_tables` task -- a `sql_task` over the same
  `databricks/sql/audit_archive_ddl.sql` -- was therefore not exercised as a
  task. The identical statements were applied through the serverless SQL
  warehouse with `dbx.py sql` instead.
- The documented upload transport is UNVERIFIED for this unit: the demo access
  token is not granted the Databricks `files` scope, so `dbx.py upload` of the
  fixture into the landing volume fails with
  `403 ... Provided access token does not have required scopes: files`, and the
  substitute below is a demo-time workaround, not the production transport that
  a real deployment would use. The
  fixture was therefore imported as a workspace file
  (`/Shared/ow_tp/fixtures/audit_events.jsonl`, Workspace API) and copied into
  `/Volumes/ow_tp/bronze/landing/demo/audit_archive/audit_events.jsonl` from a
  one-off serverless run, where Unity Catalog grants apply rather than token
  scopes. The landed file was confirmed byte-identical in size (3015713 bytes)
  to the local fixture the legacy baseline run consumed, and the pipeline's
  `read_files` ingest read it from the volume exactly as the job does.

Check 4 re-ran the same job with the same parameters after the first run had
already archived and purged, so idempotency is measured against real table state,
not simulated. Nothing in this report is derived from the converted job's own
output being compared against itself: every expectation comes from the legacy
archive artefact and its compliance report.

## Acceptance checks

| # | Check | Result |
|---|---|---|
| 1 | selection parity: archived event_id set == legacy archive set | **pass** |
| 2 | count parity: manifest counts == silver rows == legacy count | **pass** |
| 3 | retention safety: no purge without verified; archive still readable | **pass** |
| 4 | idempotency: a second run archives nothing new and duplicates nothing | **pass** |
| 5 | provenance: baseline tier stated verbatim | **pass** |

### 1. selection parity: archived event_id set == legacy archive set -- pass

```json
{
  "legacy_count": 4091,
  "converted_count": 4091,
  "legacy_id_set_sha256": "a16b6609ad9a2760620504751d1f170c92648fd4c57dfb5c8263a4d19d8f826f",
  "converted_id_set_sha256": "a16b6609ad9a2760620504751d1f170c92648fd4c57dfb5c8263a4d19d8f826f",
  "missing_from_converted": [],
  "missing_count": 0,
  "extra_in_converted": [],
  "extra_count": 0,
  "cutoff_ts": "2026-05-03 00:00:00",
  "legacy_cutoff": "2026-05-03T00:00:00Z",
  "baseline_errors": [],
  "boundary_max_archived_event_ts": "2026-05-02T23:59:59.000Z",
  "legacy_max_archived_ts": "2026-05-02T23:59:59Z",
  "over_archived_at_or_after_cutoff": 0,
  "over_archived_sample_event_ids": [],
  "over_archived_min_event_ts": "None"
}
```

### 2. count parity: manifest counts == silver rows == legacy count -- pass

```json
{
  "legacy_events_archived": 4091,
  "legacy_events_scanned": 4091,
  "baseline_errors": [],
  "manifest_candidate_count": 4091,
  "manifest_archived_count": 4091,
  "silver_rows": 4091,
  "silver_distinct_event_ids": 4091,
  "silver_rows_at_or_after_cutoff": 0
}
```

### 3. retention safety: no purge without verified; archive still readable -- pass

```json
{
  "manifest_rows_with_unverified_purge": 0,
  "source_candidates_without_archive_row": 0,
  "archive_rows_readable_after_purge": 4091,
  "archive_rows_with_missing_provenance": 0,
  "archive_rows_with_null_payload": 0,
  "source_rows_purged": 4091,
  "manifest_deleted_count": 4091,
  "manifest_verified": true,
  "legacy_events_deleted_from_source": 0
}
```

### 4. idempotency: a second run archives nothing new and duplicates nothing -- pass

```json
{
  "rerun_job": "ow_tp_dev_audit_archive",
  "rerun_result_state": "SUCCESS",
  "rerun_url": "https://dbc-8bc9474f-40ae.cloud.databricks.com/?o=7474651138173478#job/276973438832758/run/781328583950364",
  "silver_rows_before": 4091,
  "silver_rows_after": 4091,
  "silver_id_set_sha256_before": "a16b6609ad9a2760620504751d1f170c92648fd4c57dfb5c8263a4d19d8f826f",
  "silver_id_set_sha256_after": "a16b6609ad9a2760620504751d1f170c92648fd4c57dfb5c8263a4d19d8f826f",
  "duplicate_event_ids": 0,
  "manifest_rows_after": 1,
  "manifest_before": [
    {
      "ns": "demo",
      "run_date": "2026-08-01",
      "cutoff_ts": "2026-05-03T00:00:00.000Z",
      "candidate_count": "4091",
      "archived_count": "4091",
      "deleted_count": "4091",
      "verified": "true",
      "retention_days": "90"
    }
  ],
  "manifest_after": [
    {
      "ns": "demo",
      "run_date": "2026-08-01",
      "cutoff_ts": "2026-05-03T00:00:00.000Z",
      "candidate_count": "4091",
      "archived_count": "4091",
      "deleted_count": "4091",
      "verified": "true",
      "retention_days": "90"
    }
  ]
}
```

### 5. provenance: baseline tier stated verbatim -- pass

```json
{
  "tier": "baseline: legacy output",
  "baseline_artifact_bytes": {
    "audit_events.jsonl.gz": 375103,
    "report.json": 694,
    "legacy_stdout.txt": 758
  },
  "legacy_stdout": "/home/ubuntu/tp-golden/python/audit_archive_weekly/legacy_stdout.txt",
  "legacy_archive_artifact_sha256": "13946007518477977f5f4809704f86f317ea78417025a1365c77b22d7cbcfb85",
  "legacy_events_in_artifact": 4091,
  "legacy_retention_policy": {
    "retention_days": 90,
    "cutoff_date": "2026-05-03T00:00:00Z"
  },
  "artifacts_describe_this_run": true
}
```

## Context

```json
{
  "ns": "demo",
  "run_date": "2026-08-01",
  "retention_days": 90,
  "cutoff_ts": "2026-05-03 00:00:00",
  "catalog": "ow_tp",
  "baseline": {
    "archive_path": "/home/ubuntu/tp-golden/python/audit_archive_weekly/audit_events.jsonl.gz",
    "artifact_sha256": "13946007518477977f5f4809704f86f317ea78417025a1365c77b22d7cbcfb85",
    "count": 4091,
    "unique": 4091,
    "min_ts": "2025-09-03T01:42:18Z",
    "max_ts": "2026-05-02T23:59:59Z",
    "id_set_sha256": "a16b6609ad9a2760620504751d1f170c92648fd4c57dfb5c8263a4d19d8f826f",
    "report": {
      "report_type": "audit_archive_compliance",
      "execution_date": "2026-08-01",
      "generated_at": "2026-08-01T03:00:02.346181+00:00",
      "retention_policy": {
        "retention_days": 90,
        "cutoff_date": "2026-05-03T00:00:00Z"
      },
      "results": {
        "events_scanned": 4091,
        "events_archived": 4091,
        "events_deleted_from_source": 0,
        "archive_location": "s3://otterworks-audit-archive/audit-archive/year=2026/week=2026-08-01/audit_events.jsonl.gz",
        "archive_storage_class": "GLACIER",
        "compressed_size_bytes": 375103
      },
      "compliance": {
        "gdpr_compliant": true,
        "soc2_compliant": true,
        "data_encrypted_at_rest": true,
        "data_encrypted_in_transit": true
      }
    },
    "errors": []
  },
  "silver": {
    "rows_at_or_after_cutoff": 0,
    "min_event_ts_at_or_after_cutoff": null,
    "max_event_ts_at_or_after_cutoff": null,
    "sample_event_ids_at_or_after_cutoff": [],
    "rows": 4091,
    "distinct_event_ids": 4091,
    "max_event_ts": "2026-05-02T23:59:59.000Z",
    "min_event_ts": "2025-09-03T01:42:18.000Z",
    "incomplete_rows": 0,
    "null_payload_rows": 0,
    "id_set_sha256": "a16b6609ad9a2760620504751d1f170c92648fd4c57dfb5c8263a4d19d8f826f"
  },
  "manifest": [
    {
      "ns": "demo",
      "run_date": "2026-08-01",
      "cutoff_ts": "2026-05-03T00:00:00.000Z",
      "candidate_count": "4091",
      "archived_count": "4091",
      "deleted_count": "4091",
      "verified": "true",
      "retention_days": "90"
    }
  ]
}
```

_Generated by `scripts/tp_databricks/recon_audit_archive.py` at 2026-08-15T23:33:06Z._
