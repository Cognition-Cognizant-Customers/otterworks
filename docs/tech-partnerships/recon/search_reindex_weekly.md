baseline: legacy output

# Recon: ow_tp_search_reindex vs etl/scripts/search_reindex_weekly.py (ns=demo)

- result: **green**
- legacy run: exit 0, stdout captured at `/home/ubuntu/tp-golden/python/search_reindex_weekly/stdout.txt`
- golden output: the MeiliSearch `documents` / `files` indexes built by the legacy run on this machine
- converted output: `ow_tp.silver.search_index_documents` / `ow_tp.gold.search_reindex_summary`

## Disclosures

- **Transport**: extract -> `scripts/tp_databricks/load_bronze_via_sql.py` -> the same bronze table over the serverless warehouse. The volume upload is unavailable to this unit: the demo PAT carries `sql, unity-catalog, jobs, secrets, workspace` scopes and the Files API answers `403 ... required scopes: files`. Only the transport differs -- the envelopes, the bronze table and every downstream statement are the pipeline's own, and `publish_index` (the build-then-swap logic under test) ran as a real serverless job task.
- **Sample selection (check 2)**: ids are drawn from the legacy index itself using `random.Random(int(sha256('search_reindex_weekly:<ns>:<entity_type>').hexdigest()[:16], 16))` over the lexicographically sorted id list, 50 per entity type. Fixed seed, fixed ordering, fixed before any value is compared -- the sample is not chosen to favour the conversion.
- **Null/default normalization (check 2)**: the legacy script defaulted absent source fields to `""` / `[]`; the converted projection stores SQL NULL for the same absent field. Those are treated as equal, and every application is counted as `null_default_normalizations`. In this run they are all the `tags` field, which neither service API returns for the seeded corpus.
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
  "null_default_normalizations": 50
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
  "null_default_normalizations": 50
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
  "serving_counts_after_failed_run": {
    "file": 9461,
    "document": 1933
  },
  "legacy_counts": {
    "document": 1933,
    "file": 9461
  },
  "index_intact": true,
  "run_url": "https://dbc-8bc9474f-40ae.cloud.databricks.com/?o=7474651138173478#job/749284665997898/run/903323673811358"
}
```

## check 4 — rerun idempotency — PASS

```json
{
  "rerun_result_state": "SUCCESS",
  "counts_after_rerun": {
    "file": 9461,
    "document": 1933
  },
  "counts_after_first_run": {
    "file": 9461,
    "document": 1933
  },
  "legacy_counts": {
    "document": 1933,
    "file": 9461
  },
  "duplicate_entity_ids": 0,
  "run_url": "https://dbc-8bc9474f-40ae.cloud.databricks.com/?o=7474651138173478#job/749284665997898/run/145054565034633"
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
