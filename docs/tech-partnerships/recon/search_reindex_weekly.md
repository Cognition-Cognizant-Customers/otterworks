baseline: legacy output

# Recon: ow_tp_search_reindex vs etl/scripts/search_reindex_weekly.py (ns=demo)

- result: **green**
- legacy run: exit 0, stdout captured at `/home/ubuntu/tp-golden/python/search_reindex_weekly/stdout.txt`
- golden output: the MeiliSearch `documents` / `files` indexes built by the legacy run on this machine
- converted output: `ow_tp.silver.search_index_documents` / `ow_tp.gold.search_reindex_summary`

## Disclosures

- **Transport**: extract -> `scripts/tp_databricks/load_bronze_via_sql.py` -> the same bronze table over the serverless warehouse. **The documented landing-volume upload path is UNVERIFIED.** It cannot be executed by this unit: the demo PAT carries `sql, unity-catalog, jobs, secrets, workspace` scopes and the Files API answers `PUT /api/2.0/fs/files/Volumes/ow_tp/bronze/landing/... -> 403: {"error_code":403,"message":"Provided access token does not have required scopes: files"}`, and the parent session has confirmed no files-scoped token is coming. This loader is a test transport, not the production one: the envelopes, the bronze table and every downstream statement are the pipeline's own, and `publish_index` -- the build-then-swap logic under test -- ran as a real serverless job task, but `ingest_bronze`'s volume read is covered by review only. A defect on that unexecuted path (the manifest read via `spark.read.text`, which silently skips leaf files whose names begin with `_`) was found in review, not by a run; it is fixed and still unexecuted.
- **Guards not exercised by this corpus**: eight defensive paths are reasoned and reviewed but never entered by a run on this data, and none of them contributes to any PASS below -- the empty-extract guard and the erase-an-existing-entity-type guard in `ingest_bronze`, the shrink-to-zero guard in `publish_index`, the corresponding empty-manifest and erase-an-existing-entity-type guards in the SQL fallback loader, wait-timeout cancellation and terminal-state teardown in `run_search_reindex_dev.py`, and the minimum-observed-total completeness and 0600 artifact-mode guards in `extract_search_sources.py`. They fire only on a degenerate extract or lifecycle edge case, which the seeded fixture does not produce; the checks below all ran on a full 1,933 / 9,461 corpus.
- **Sample selection (check 2)**: ids are drawn from the legacy index itself using `random.Random(int(sha256('search_reindex_weekly:<ns>:<entity_type>').hexdigest()[:16], 16))` over the lexicographically sorted id list, 50 per entity type. Fixed seed, fixed ordering, fixed before any value is compared -- the sample is not chosen to favour the conversion.
- **Null/default normalization (check 2)**: the legacy script defaulted absent source fields to `""` / `[]`; where the converted projection stores SQL NULL for the same absent field the two are treated as equal. Every application is counted per entity type as `null_default_normalizations` below, so the extent of the leniency is visible rather than implied -- zero there means the two sides matched on representation as well as on value.
- **Timestamp normalization (check 2)**: `created_at` and `updated_at` values are compared as instants, forgiving offset-suffix form (`Z` vs `+00:00`), separator form (space vs `T`), and fractional-second precision (`2025-12-03T20:40:07Z` vs `2025-12-03T20:40:07.000Z`); offset-less text is treated as UTC, which is an assumption about the legacy side's representation rather than a verified fact. All 100 of 100 sampled timestamp comparisons per entity type were accepted this way, reported as `timestamp_normalizations` below.
- **Count snapshots (checks 3b and 4)**: the serving counts each run is judged on are read by `run_search_reindex_dev.py` the moment that run finishes and stored in its run artifact; recon reads those recorded values and compares the live table on top. Reading both sides at report time would make the equality hold by construction and could never detect drift.
- **Counts**: the seed generator creates 2,000 documents, 67 of them soft-deleted and therefore never returned by `/api/v1/documents`; the legacy run indexed 1,933, so parity is measured against that legacy output rather than the raw seed total. Files: 10,000 DynamoDB items, 9,461 API-visible once trashed items are excluded.

## check 1 — count parity per entity type — PASS

```json
{
  "rows": [
    {
      "entity_type": "document",
      "legacy": 1933,
      "converted": 1933,
      "match": true
    },
    {
      "entity_type": "file",
      "legacy": 9461,
      "converted": 9461,
      "match": true
    }
  ]
}
```

## check 2 — sample content parity (document) — PASS

```json
{
  "entity_type": "document",
  "sampled": 50,
  "compared_fields": 300,
  "missing_from_converted": [],
  "mismatches": [],
  "mismatch_count": 0,
  "null_default_normalizations": 0,
  "timestamp_normalizations": 100
}
```

## check 2 — sample content parity (file) — PASS

```json
{
  "entity_type": "file",
  "sampled": 50,
  "compared_fields": 400,
  "missing_from_converted": [],
  "mismatches": [],
  "mismatch_count": 0,
  "null_default_normalizations": 0,
  "timestamp_normalizations": 100
}
```

## check 3a — gold summary flags — PASS

```json
{
  "rows": [
    {
      "run_date": "2026-08-15",
      "entity_type": "document",
      "source_count": 1933,
      "indexed_count": 1933,
      "counts_match": true,
      "swap_completed": true
    },
    {
      "run_date": "2026-08-15",
      "entity_type": "file",
      "source_count": 9461,
      "indexed_count": 9461,
      "counts_match": true,
      "swap_completed": true
    }
  ]
}
```

## check 3b — forced source failure leaves the index intact — PASS

```json
{
  "run_result_state": "FAILED",
  "ingest_result_state": "FAILED",
  "publish_result_state": "UPSTREAM_FAILED",
  "serving_counts_at_failed_run_end": {
    "file": 9461,
    "document": 1933
  },
  "snapshot_taken_at": "2026-08-15T23:40:02.162131+00:00",
  "serving_counts_now": {
    "file": 9461,
    "document": 1933
  },
  "legacy_counts": {
    "document": 1933,
    "file": 9461
  },
  "index_intact": true,
  "run_url": "https://dbc-8bc9474f-40ae.cloud.databricks.com/?o=7474651138173478#job/956664464553421/run/1091127948762526"
}
```

## check 4 — rerun idempotency — PASS

```json
{
  "first_run_result_state": "SUCCESS",
  "rerun_result_state": "SUCCESS",
  "counts_at_first_run_end": {
    "file": 9461,
    "document": 1933
  },
  "first_snapshot_taken_at": "2026-08-15T23:36:28.572761+00:00",
  "counts_at_rerun_end": {
    "file": 9461,
    "document": 1933
  },
  "rerun_snapshot_taken_at": "2026-08-15T23:37:26.125134+00:00",
  "counts_live_now": {
    "file": 9461,
    "document": 1933
  },
  "legacy_counts": {
    "document": 1933,
    "file": 9461
  },
  "duplicate_entity_ids": 0,
  "first_run_url": "https://dbc-8bc9474f-40ae.cloud.databricks.com/?o=7474651138173478#job/956664464553421/run/758912243476638",
  "run_url": "https://dbc-8bc9474f-40ae.cloud.databricks.com/?o=7474651138173478#job/956664464553421/run/77502888589612"
}
```

## check 5 — baseline provenance — PASS

```json
{
  "baseline": "baseline: legacy output",
  "exit_code": "0",
  "stdout_path": "/home/ubuntu/tp-golden/python/search_reindex_weekly/stdout.txt"
}
```
