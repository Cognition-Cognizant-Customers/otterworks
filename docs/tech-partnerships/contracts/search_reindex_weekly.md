# Contract: `search_reindex_weekly.py` → `ow_tp_search_reindex`

Read [README.md](README.md) and [_python_wave_baseline.md](_python_wave_baseline.md) first.

| | |
|---|---|
| Source | `etl/scripts/search_reindex_weekly.py` |
| Language / vintage | Python 2014-vintage (requests + MeiliSearch) |
| Legacy schedule | Sunday 04:00 UTC |
| Converted job | `ow_tp_search_reindex` (`infrastructure/terraform-databricks/jobs_search_reindex.tf`) |

## Deficiencies this conversion must retire

**Deletes the search indices before it knows it can rebuild them** — the legacy run below
cleared both indices and then died on the first extract page, leaving search empty in
production until someone noticed (→ build-then-swap, never clear-then-hope). Plus:
credentials and service URLs hardcoded; no retry on the paginated API reads; `print()`
logging; silent `except: pass`; count "validation" that logs a mismatch and exits zero; no
idempotency; no alerting.

## Target

| Object | Contents |
|---|---|
| `ow_tp.bronze.search_documents_raw` | documents and files as extracted: `ns`, `entity_type` (`document`/`file`), `entity_id`, `payload`, `extracted_at` |
| `ow_tp.silver.search_index_documents` | the index-ready projection, one row per entity, typed and deduplicated, with the fields the index actually needs |
| `ow_tp.gold.search_reindex_summary` | per run: `ns`, `run_date`, `entity_type`, `source_count`, `indexed_count`, `counts_match BOOLEAN`, `swap_completed BOOLEAN`. A mismatch must fail the run, not log a line. |

## Baseline

Legacy run on this VM **partially executed**
(`/home/ubuntu/tp-golden/python/search_reindex_weekly/`): it cleared and recreated the
MeiliSearch `documents` and `files` indices, configured both, then failed at extraction:

```text
FATAL: HTTPConnectionPool(host='localhost', port=8083): Max retries exceeded with url:
/api/v1/documents?page=1&size=100 ... Connection refused
```

It needs `document-service` on `:8083` (and `file-service` on `:8082`). Tier 1 attempt:
bring those two services up via the repo's normal path and re-run — **do not modify** the
services, compose files, or anything else on the golden app path; running them is fine,
changing them is not. The underlying data is the seeded Postgres slice (`otterworks_demo`:
2,000 documents, 13,876 versions) on `localhost:55432`. If the services cannot be brought
up, fall back to the seed-manifest tier: source counts come from the seeded stores.

## Acceptance checks (`scripts/tp_databricks/recon_search_reindex.py`)

1. Count parity per entity type: `silver.search_index_documents` row counts equal the
   baseline source counts (2,000 documents for `ns='demo'` from the seed, or the legacy
   extract count if tier 1) — exact, no tolerance.
2. Content parity on a deterministic sample: for a fixed, seed-derived sample of entity
   ids, every projected field matches the source record. State how the sample was chosen.
3. `gold.search_reindex_summary` shows `counts_match = true` and `swap_completed = true`,
   and a forced source failure leaves the previous index intact and the run failed —
   demonstrate this, since it is the deficiency this unit exists to retire.
4. Re-run; assert counts unchanged and no duplicate entity ids (idempotency).
5. Report states the baseline tier verbatim.
