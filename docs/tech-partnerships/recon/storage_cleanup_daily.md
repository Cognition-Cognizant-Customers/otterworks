baseline: legacy output

# Recon: `storage_cleanup_daily.py` -> `ow_tp_storage_cleanup` (`ns=other`)

Generated 2026-08-16T01:19:25.137979+00:00 by `scripts/tp_databricks/recon_storage_cleanup.py`.

**Result: partial** -- 1/5 checks passed.

## Baseline provenance

Tier 1. The legacy script names `s3://otterworks-file-storage/files/`, which no local
fixture provided (`NoSuchBucket`). What had to be stood up on this VM:

- `make infra-up` plus the documented workaround for the occupied host port 5432:
  Postgres runs in container `otterworks-postgres-alt` on 55432 (`DB_PORT=55432`).
- `make seed-legacy NS=demo` and `make seed-legacy-validate NS=demo` (15/15 checks).
- `scripts/tp_databricks/fixture_storage_cleanup.py build --ns demo`: creates the missing
  `otterworks-file-storage` and `otterworks-file-quarantine` buckets,
  writes 0 live objects from the seeded metadata keys, and
  plants 25 objects with no metadata row
  (854277 bytes) under the `files/` prefix the script lists.
- Then the **unedited** `etl/scripts/storage_cleanup_daily.py` was run (nothing under `etl/`
  was modified) and what it moved into the quarantine bucket is the golden orphan set:
  `/home/ubuntu/tp-golden/python/storage_cleanup_daily/legacy_stdout.txt`, `legacy_report.json`, `quarantined_keys.txt`.

Legacy run, for the record:

```json
{
  "report_type": "storage_cleanup",
  "report_date": "2026-08-15",
  "generated_at": "2026-08-15T23:56:31.791268+00:00",
  "inventory": {
    "total_objects": 25,
    "total_size_bytes": 635837,
    "total_size_gb": 0.0006
  },
  "orphans": {
    "orphaned_objects": 25,
    "orphaned_bytes": 635837,
    "orphaned_size_gb": 0.0006,
    "orphan_percentage": 100.0
  },
  "cleanup": {
    "objects_quarantined": 25,
    "objects_failed": 0,
    "quarantine_bucket": "otterworks-file-quarantine"
  },
  "savings": {
    "storage_freed_gb": 0.0006,
    "estimated_monthly_savings_usd": 0.0
  }
}
```

## Landing transport: UNVERIFIED

The documented bronze landing path -- writing extracts to the volume
`/Volumes/ow_tp/bronze/landing` via `dbx.py upload` -- is **UNVERIFIED by this recon**.
The demo PAT lacks the `files` scope, so every upload attempt returned, verbatim:

```text
403: {"error_code":403,"message":"Provided access token does not have required scopes: files"}
```

The in-Databricks landing this recon actually used is `INSERT` statements executed on the
existing serverless SQL warehouse by `scripts/tp_databricks/extract_storage_cleanup.py`,
which produce the same bronze rows. That is a workaround for a missing token scope and is
**not** presented as the production transport: the volume path stays the documented one and
remains untested here. No acceptance check below was weakened, relaxed or skipped because of
this -- the checks compare the same bronze contents either way.

## Acceptance checks

### 0. Notebook DDL and the committed DDL file are the same statements -- PASS

```text
PASS DDL statements: baseline=['CREATE TABLE IF NOT EXISTS ow_tp.bronze.storage_objects_raw ( ns STRING, bucket STRING, key STRING, size_bytes BIGINT, legacy_attributed BOOLEAN, last_modified TIMESTAMP, listed_at TIMESTAMP)', 'ALTER TABLE ow_tp.bronze.storage_objects_raw ADD COLUMNS (legacy_attributed BOOLEAN)', 'CREATE TABLE IF NOT EXISTS ow_tp.bronze.file_metadata_raw ( ns STRING, file_id STRING, storage_key STRING, owner_id STRING, size_bytes BIGINT, created_at TIMESTAMP)', 'CREATE TABLE IF NOT EXISTS ow_tp.bronze.storage_extract_manifest ( ns STRING, scenario STRING, source_bucket STRING, source_table STRING, objects_expected BIGINT, objects_bytes BIGINT, metadata_expected BIGINT, metadata_read_complete BOOLEAN, extracted_at TIMESTAMP, loaded_at TIMESTAMP)', 'CREATE TABLE IF NOT EXISTS ow_tp.silver.storage_orphans ( ns STRING, bucket STRING, key STRING, size_bytes BIGINT, orphan_reason STRING, detected_at TIMESTAMP, metadata_read_ok BOOLEAN, scenario STRING)', 'CREATE TABLE IF NOT EXISTS ow_tp.gold.storage_cleanup_savings ( ns STRING, run_date DATE, objects_scanned BIGINT, metadata_rows BIGINT, orphan_count BIGINT, orphan_bytes BIGINT, quarantined_count BIGINT, dry_run BOOLEAN, scenario STRING, metadata_read_ok BOOLEAN, generated_at TIMESTAMP)'] converted=['CREATE TABLE IF NOT EXISTS ow_tp.bronze.storage_objects_raw ( ns STRING, bucket STRING, key STRING, size_bytes BIGINT, legacy_attributed BOOLEAN, last_modified TIMESTAMP, listed_at TIMESTAMP)', 'ALTER TABLE ow_tp.bronze.storage_objects_raw ADD COLUMNS (legacy_attributed BOOLEAN)', 'CREATE TABLE IF NOT EXISTS ow_tp.bronze.file_metadata_raw ( ns STRING, file_id STRING, storage_key STRING, owner_id STRING, size_bytes BIGINT, created_at TIMESTAMP)', 'CREATE TABLE IF NOT EXISTS ow_tp.bronze.storage_extract_manifest ( ns STRING, scenario STRING, source_bucket STRING, source_table STRING, objects_expected BIGINT, objects_bytes BIGINT, metadata_expected BIGINT, metadata_read_complete BOOLEAN, extracted_at TIMESTAMP, loaded_at TIMESTAMP)', 'CREATE TABLE IF NOT EXISTS ow_tp.silver.storage_orphans ( ns STRING, bucket STRING, key STRING, size_bytes BIGINT, orphan_reason STRING, detected_at TIMESTAMP, metadata_read_ok BOOLEAN, scenario STRING)', 'CREATE TABLE IF NOT EXISTS ow_tp.gold.storage_cleanup_savings ( ns STRING, run_date DATE, objects_scanned BIGINT, metadata_rows BIGINT, orphan_count BIGINT, orphan_bytes BIGINT, quarantined_count BIGINT, dry_run BOOLEAN, scenario STRING, metadata_read_ok BOOLEAN, generated_at TIMESTAMP)']
```

### 1. Orphan-set parity: exact (bucket, key) set equality -- FAIL

```text
FAIL planted set == legacy quarantined set: baseline={('otterworks-file-storage', 'files/07462a9b-ea16-4563-bcec-9ba1f6c208eb/c28d78ea-dd33-4f7b-ba7b-869655f93e0e'), ('otterworks-file-storage', 'files/7387cd4a-a480-4ff5-874e-4a9b37dd5561/c4ebf31b-85d7-465d-9bb4-a8b8cef5ed84'), ('otterworks-file-storage', 'files/a1253df1-7131-480b-bf82-b8f6df678f64/f93296fe-29fd-4f79-a7cf-fcdb6993934f'), ('otterworks-file-storage', 'files/cccf6ab3-8fda-449c-9f8a-f754134a6842/c0fd0cbb-534b-414c-a2f1-fa08a60a2ed6'), ('otterworks-file-storage', 'files/45b4af51-b471-465b-bf5d-8f0fa6fdbe99/24b81213-d20d-40ae-81e4-51a65582dfc3'), ('otterworks-file-storage', 'files/78df8b63-1b32-43d1-9dca-03e19694e925/0776f99d-75c5-4410-a6e0-a0c67a158d1d'), ('otterworks-file-storage', 'files/d940b7f1-1d6e-4e1d-ba91-c727e5f712cb/6f8dcfb4-d016-40cc-a4d9-aacaf77750c8'), ('otterworks-file-storage', 'files/08e492d0-b0af-4f29-ab3e-f810f02811c3/c91401bc-22cd-4d4f-8b05-fa6cfcee781e'), ('otterworks-file-storage', 'files/9b259a56-0bc4-43b4-b858-223cac2ee2bb/25cd01a5-825a-43ac-926a-ad0f82e4388b'), ('otterworks-file-storage', 'files/4fc4e65c-d2c3-4035-928d-764ac947b2fa/1c382f81-c917-4afe-aec7-24837b5e8943'), ('otterworks-file-storage', 'files/126b032a-407a-490e-b385-9e6c7b410162/d44229e1-65f9-4a31-ad3f-df491a5ecdf9'), ('otterworks-file-storage', 'files/fd81181d-b218-44b8-86e1-6fb8608d9c0c/9cdb02c9-e206-4e14-b137-ea740a3783ac'), ('otterworks-file-storage', 'files/54b1a903-d814-4234-ba29-37fbe7235d93/372276da-a957-44ef-91f7-71c819226c15'), ('otterworks-file-storage', 'files/8a480cf2-d86f-4115-a6a9-b14e627e4036/4299ea15-12f5-4144-a8f6-50396efda02b'), ('otterworks-file-storage', 'files/1c074841-4ec3-40f8-8adb-883ae0cf4890/f768950d-fbd0-4175-a675-75084cfa65a2'), ('otterworks-file-storage', 'files/44b10749-def8-4f1d-a551-ab676cb9e8cc/0c5d307d-8572-448b-87eb-af8d26e029ce'), ('otterworks-file-storage', 'files/4ebba49b-182c-45d5-822f-8a4051fc1999/a1e44d50-9ad9-4c13-83f5-f27dfd27c4e8'), ('otterworks-file-storage', 'files/852285d9-1c8a-49c1-9379-ea950915ea7e/44a1466d-d719-481d-ae6b-b98f12dd9072'), ('otterworks-file-storage', 'files/90cbe207-12dd-4253-b3b9-a5bfcbfc190c/1bc68d16-fe3f-413d-a2b2-f747d11b7ffd'), ('otterworks-file-storage', 'files/a0bec862-f989-43f6-8106-9431a5780364/88efc055-c295-4d63-834e-ecf1e7e9db28'), ('otterworks-file-storage', 'files/f2c7d3ea-5480-400c-a0f4-ece22569f630/b90a49ab-9bc1-4e4c-8a63-d622b5e25585'), ('otterworks-file-storage', 'files/d133be60-b7e4-4d1e-9f76-03e2dae5ac50/2244cb36-5175-409c-855a-652f36266c81'), ('otterworks-file-storage', 'files/ee8f718d-58a6-469a-a718-48ea67c5f522/84f46cba-fd42-4d35-8dcc-e96eddf759b7'), ('otterworks-file-storage', 'files/c223a33b-fd06-4b49-a33a-c7e8028603fe/1f73b4c1-dae9-4148-b7c5-5a6bc23a0b01'), ('otterworks-file-storage', 'files/ab421758-0599-43de-b3f7-c54eedea9f2e/fdebdbe1-c10f-4201-8f11-00f6f80a9bc8')} converted={('otterworks-file-storage', 'files/5d7941cb-7eac-4990-8135-f065c28e691c/62798a28-4440-4423-bf95-6c849e7222e2'), ('otterworks-file-storage', 'files/3e972cae-da9c-42da-a956-6dcc790785bc/007c2a54-fb64-4f7b-aca2-d63def141274'), ('otterworks-file-storage', 'files/53128dde-59ca-4082-9503-992db1f788b7/4f0e84fa-05f6-4b03-9952-d6b93612a1d1'), ('otterworks-file-storage', 'files/502e3a5f-fe69-4186-950f-a91d39ed9150/7a5f253f-b421-4b49-a485-c774d8847a11'), ('otterworks-file-storage', 'files/d0451c10-89c0-40f4-a56d-b0afab85b0e2/8f7a4897-e57b-4ecb-978a-74811f4cb71b'), ('otterworks-file-storage', 'files/55841ad0-37ff-4705-9e95-a32df32a3196/e5053e35-d260-4a19-994a-65b1fb94305d'), ('otterworks-file-storage', 'files/00aa6538-8651-450a-a444-d20fd8ebf46b/8978a9e7-4780-40c8-8a04-9bf090836a89'), ('otterworks-file-storage', 'files/83eabf79-84de-4ba4-bd8b-9655c8e766a4/1d55849b-8ef5-4a6a-88e8-e7596a320977'), ('otterworks-file-storage', 'files/90338296-47ac-4943-9e72-25bf0807e3a4/a761ee97-bf8d-4126-ab39-165150acb302'), ('otterworks-file-storage', 'files/2ce1ad33-c76e-45ed-aab0-fa72a81cb62b/10766d40-49c5-4db0-abb9-5fc38c03a75f'), ('otterworks-file-storage', 'files/375be158-62fb-4f84-b789-08de2ede41e9/b8481879-6871-4d2a-b6d6-b346dea8846c'), ('otterworks-file-storage', 'files/f2062dba-61d1-425a-9e7d-efd1f3c7b04a/65b7bba4-ae0f-44e5-859c-cb7f1853aac5'), ('otterworks-file-storage', 'files/0b554a95-263a-4d3b-a21d-c1c02f6a0258/66a27ea1-fbc0-4d0e-9cfc-3d01d6b2297b'), ('otterworks-file-storage', 'files/ace9c018-1315-4bbc-9e21-d9e5900a907b/b5f23f85-c42e-4b1e-bcb0-161df81d9127'), ('otterworks-file-storage', 'files/f55038e8-c5ef-48a6-8e84-01d1627224a5/85a6db5a-5c1b-4ec5-811d-d780c6cfc1c5'), ('otterworks-file-storage', 'files/262d3f60-7359-44c6-83df-1169752614e4/3f593eca-6c44-4ec8-962f-c5c4657758bd'), ('otterworks-file-storage', 'files/7a2c5e40-2389-4b70-bea0-5803ec5dc6e4/ef19d9ef-6953-437c-880f-c53ef16dcaf1'), ('otterworks-file-storage', 'files/1f38312c-c725-44fb-b440-0e21df1a115c/ac9ca237-9999-4965-b980-d1223a0b233f'), ('otterworks-file-storage', 'files/460f24b8-5328-494d-9426-f2615467976b/18dc39ef-e377-4c1b-89b2-72faf34afc55'), ('otterworks-file-storage', 'files/43e4e26d-59c7-4e1c-a29c-84413634e5e5/3c2efa47-d38b-4abc-98db-761c29afabcb'), ('otterworks-file-storage', 'files/e8b8a9fe-500c-44c5-8e72-93b229d5fa4c/8755d2c4-d7e7-47ed-a888-0b10c01a5cce'), ('otterworks-file-storage', 'files/88adafab-7f59-419a-ab68-7ed850b31201/31387716-29eb-4eab-b0db-00342bb0898b'), ('otterworks-file-storage', 'files/cb951454-600a-459e-96e3-50cd74f9dfaf/ef6ec1d2-73a1-48a5-98ac-da162aae5c3a'), ('otterworks-file-storage', 'files/945624f4-beb7-463b-b60c-7a115382993a/4c908afa-d77e-461c-b1fa-ba0e2f10ca91'), ('otterworks-file-storage', 'files/d51ba131-9906-4128-ad7d-c0ed0d8b0367/65f049d3-4b09-400f-aba7-8ae592a79568')}
FAIL legacy quarantined set == silver confirmed orphans: baseline={('otterworks-file-storage', 'files/5d7941cb-7eac-4990-8135-f065c28e691c/62798a28-4440-4423-bf95-6c849e7222e2'), ('otterworks-file-storage', 'files/3e972cae-da9c-42da-a956-6dcc790785bc/007c2a54-fb64-4f7b-aca2-d63def141274'), ('otterworks-file-storage', 'files/53128dde-59ca-4082-9503-992db1f788b7/4f0e84fa-05f6-4b03-9952-d6b93612a1d1'), ('otterworks-file-storage', 'files/502e3a5f-fe69-4186-950f-a91d39ed9150/7a5f253f-b421-4b49-a485-c774d8847a11'), ('otterworks-file-storage', 'files/d0451c10-89c0-40f4-a56d-b0afab85b0e2/8f7a4897-e57b-4ecb-978a-74811f4cb71b'), ('otterworks-file-storage', 'files/55841ad0-37ff-4705-9e95-a32df32a3196/e5053e35-d260-4a19-994a-65b1fb94305d'), ('otterworks-file-storage', 'files/00aa6538-8651-450a-a444-d20fd8ebf46b/8978a9e7-4780-40c8-8a04-9bf090836a89'), ('otterworks-file-storage', 'files/83eabf79-84de-4ba4-bd8b-9655c8e766a4/1d55849b-8ef5-4a6a-88e8-e7596a320977'), ('otterworks-file-storage', 'files/90338296-47ac-4943-9e72-25bf0807e3a4/a761ee97-bf8d-4126-ab39-165150acb302'), ('otterworks-file-storage', 'files/2ce1ad33-c76e-45ed-aab0-fa72a81cb62b/10766d40-49c5-4db0-abb9-5fc38c03a75f'), ('otterworks-file-storage', 'files/375be158-62fb-4f84-b789-08de2ede41e9/b8481879-6871-4d2a-b6d6-b346dea8846c'), ('otterworks-file-storage', 'files/f2062dba-61d1-425a-9e7d-efd1f3c7b04a/65b7bba4-ae0f-44e5-859c-cb7f1853aac5'), ('otterworks-file-storage', 'files/0b554a95-263a-4d3b-a21d-c1c02f6a0258/66a27ea1-fbc0-4d0e-9cfc-3d01d6b2297b'), ('otterworks-file-storage', 'files/ace9c018-1315-4bbc-9e21-d9e5900a907b/b5f23f85-c42e-4b1e-bcb0-161df81d9127'), ('otterworks-file-storage', 'files/f55038e8-c5ef-48a6-8e84-01d1627224a5/85a6db5a-5c1b-4ec5-811d-d780c6cfc1c5'), ('otterworks-file-storage', 'files/262d3f60-7359-44c6-83df-1169752614e4/3f593eca-6c44-4ec8-962f-c5c4657758bd'), ('otterworks-file-storage', 'files/7a2c5e40-2389-4b70-bea0-5803ec5dc6e4/ef19d9ef-6953-437c-880f-c53ef16dcaf1'), ('otterworks-file-storage', 'files/1f38312c-c725-44fb-b440-0e21df1a115c/ac9ca237-9999-4965-b980-d1223a0b233f'), ('otterworks-file-storage', 'files/460f24b8-5328-494d-9426-f2615467976b/18dc39ef-e377-4c1b-89b2-72faf34afc55'), ('otterworks-file-storage', 'files/43e4e26d-59c7-4e1c-a29c-84413634e5e5/3c2efa47-d38b-4abc-98db-761c29afabcb'), ('otterworks-file-storage', 'files/e8b8a9fe-500c-44c5-8e72-93b229d5fa4c/8755d2c4-d7e7-47ed-a888-0b10c01a5cce'), ('otterworks-file-storage', 'files/88adafab-7f59-419a-ab68-7ed850b31201/31387716-29eb-4eab-b0db-00342bb0898b'), ('otterworks-file-storage', 'files/cb951454-600a-459e-96e3-50cd74f9dfaf/ef6ec1d2-73a1-48a5-98ac-da162aae5c3a'), ('otterworks-file-storage', 'files/945624f4-beb7-463b-b60c-7a115382993a/4c908afa-d77e-461c-b1fa-ba0e2f10ca91'), ('otterworks-file-storage', 'files/d51ba131-9906-4128-ad7d-c0ed0d8b0367/65f049d3-4b09-400f-aba7-8ae592a79568')} converted=set()
note extras (would be deleted customer files): 0 []
note missing (orphans left behind): 25 [('otterworks-file-storage', 'files/00aa6538-8651-450a-a444-d20fd8ebf46b/8978a9e7-4780-40c8-8a04-9bf090836a89'), ('otterworks-file-storage', 'files/0b554a95-263a-4d3b-a21d-c1c02f6a0258/66a27ea1-fbc0-4d0e-9cfc-3d01d6b2297b'), ('otterworks-file-storage', 'files/1f38312c-c725-44fb-b440-0e21df1a115c/ac9ca237-9999-4965-b980-d1223a0b233f'), ('otterworks-file-storage', 'files/262d3f60-7359-44c6-83df-1169752614e4/3f593eca-6c44-4ec8-962f-c5c4657758bd'), ('otterworks-file-storage', 'files/2ce1ad33-c76e-45ed-aab0-fa72a81cb62b/10766d40-49c5-4db0-abb9-5fc38c03a75f')]
```

### 2. Byte and count parity against the legacy report -- FAIL

```text
FAIL orphan_bytes: baseline=635837 converted=0
FAIL orphan_count: baseline=25 converted=0
FAIL orphan_bytes == summed planted sizes: baseline=854277 converted=0
FAIL metadata_rows: baseline=10000 converted=0
FAIL objects_scanned under the legacy 'files/' prefix: baseline=25 converted=50
note converted objects_scanned is 50 for the whole bucket: the legacy script listed only 'files/' and never saw the other 0 objects. Broader scope, identical orphan set.
note named legacy deficiency: un-attributed objects under the shared files/ prefix remain visible but are never confirmed or quarantinable.
```

### 3. Safety guard: an incomplete metadata read quarantines nothing -- FAIL

```text
FAIL extract marks the metadata read incomplete: baseline=False converted=True
FAIL metadata rows loaded: baseline=4000 converted=0
PASS dry_run: baseline=False converted=False
PASS metadata_read_ok: baseline=False converted=False
PASS quarantined_count: baseline=0 converted=0
PASS confirmed orphan_count: baseline=0 converted=0
PASS confirmed orphan_bytes: baseline=0 converted=0
PASS rows reported as confirmed orphans: baseline=set() converted=set()
note candidates recorded for review: 50 (quarantined: 0)
PASS candidate rows carry orphan_reason=candidate_unverified_metadata_read: baseline=[['25']] converted=[['25']]
note legacy counterfactual, same defect, unedited script: with 100 of 200 metadata items unread it reported 100 orphans and quarantined 100 live customer files (see /home/ubuntu/tp-golden/python/storage_cleanup_daily/counterfactual/)
```

### 4. Idempotency: a re-run leaves the orphan set and totals unchanged -- FAIL

```text
PASS orphan set across re-runs: baseline=set() converted=set()
FAIL orphan set still equals the baseline set: baseline={('otterworks-file-storage', 'files/5d7941cb-7eac-4990-8135-f065c28e691c/62798a28-4440-4423-bf95-6c849e7222e2'), ('otterworks-file-storage', 'files/3e972cae-da9c-42da-a956-6dcc790785bc/007c2a54-fb64-4f7b-aca2-d63def141274'), ('otterworks-file-storage', 'files/53128dde-59ca-4082-9503-992db1f788b7/4f0e84fa-05f6-4b03-9952-d6b93612a1d1'), ('otterworks-file-storage', 'files/502e3a5f-fe69-4186-950f-a91d39ed9150/7a5f253f-b421-4b49-a485-c774d8847a11'), ('otterworks-file-storage', 'files/d0451c10-89c0-40f4-a56d-b0afab85b0e2/8f7a4897-e57b-4ecb-978a-74811f4cb71b'), ('otterworks-file-storage', 'files/55841ad0-37ff-4705-9e95-a32df32a3196/e5053e35-d260-4a19-994a-65b1fb94305d'), ('otterworks-file-storage', 'files/00aa6538-8651-450a-a444-d20fd8ebf46b/8978a9e7-4780-40c8-8a04-9bf090836a89'), ('otterworks-file-storage', 'files/83eabf79-84de-4ba4-bd8b-9655c8e766a4/1d55849b-8ef5-4a6a-88e8-e7596a320977'), ('otterworks-file-storage', 'files/90338296-47ac-4943-9e72-25bf0807e3a4/a761ee97-bf8d-4126-ab39-165150acb302'), ('otterworks-file-storage', 'files/2ce1ad33-c76e-45ed-aab0-fa72a81cb62b/10766d40-49c5-4db0-abb9-5fc38c03a75f'), ('otterworks-file-storage', 'files/375be158-62fb-4f84-b789-08de2ede41e9/b8481879-6871-4d2a-b6d6-b346dea8846c'), ('otterworks-file-storage', 'files/f2062dba-61d1-425a-9e7d-efd1f3c7b04a/65b7bba4-ae0f-44e5-859c-cb7f1853aac5'), ('otterworks-file-storage', 'files/0b554a95-263a-4d3b-a21d-c1c02f6a0258/66a27ea1-fbc0-4d0e-9cfc-3d01d6b2297b'), ('otterworks-file-storage', 'files/ace9c018-1315-4bbc-9e21-d9e5900a907b/b5f23f85-c42e-4b1e-bcb0-161df81d9127'), ('otterworks-file-storage', 'files/f55038e8-c5ef-48a6-8e84-01d1627224a5/85a6db5a-5c1b-4ec5-811d-d780c6cfc1c5'), ('otterworks-file-storage', 'files/262d3f60-7359-44c6-83df-1169752614e4/3f593eca-6c44-4ec8-962f-c5c4657758bd'), ('otterworks-file-storage', 'files/7a2c5e40-2389-4b70-bea0-5803ec5dc6e4/ef19d9ef-6953-437c-880f-c53ef16dcaf1'), ('otterworks-file-storage', 'files/1f38312c-c725-44fb-b440-0e21df1a115c/ac9ca237-9999-4965-b980-d1223a0b233f'), ('otterworks-file-storage', 'files/460f24b8-5328-494d-9426-f2615467976b/18dc39ef-e377-4c1b-89b2-72faf34afc55'), ('otterworks-file-storage', 'files/43e4e26d-59c7-4e1c-a29c-84413634e5e5/3c2efa47-d38b-4abc-98db-761c29afabcb'), ('otterworks-file-storage', 'files/e8b8a9fe-500c-44c5-8e72-93b229d5fa4c/8755d2c4-d7e7-47ed-a888-0b10c01a5cce'), ('otterworks-file-storage', 'files/88adafab-7f59-419a-ab68-7ed850b31201/31387716-29eb-4eab-b0db-00342bb0898b'), ('otterworks-file-storage', 'files/cb951454-600a-459e-96e3-50cd74f9dfaf/ef6ec1d2-73a1-48a5-98ac-da162aae5c3a'), ('otterworks-file-storage', 'files/945624f4-beb7-463b-b60c-7a115382993a/4c908afa-d77e-461c-b1fa-ba0e2f10ca91'), ('otterworks-file-storage', 'files/d51ba131-9906-4128-ad7d-c0ed0d8b0367/65f049d3-4b09-400f-aba7-8ae592a79568')} converted=set()
PASS gold row after re-run: baseline={'objects_scanned': 50, 'metadata_rows': 0, 'orphan_count': 0, 'orphan_bytes': 0, 'quarantined_count': 0, 'dry_run': True, 'metadata_read_ok': False} converted={'objects_scanned': 50, 'metadata_rows': 0, 'orphan_count': 0, 'orphan_bytes': 0, 'quarantined_count': 0, 'dry_run': True, 'metadata_read_ok': False}
PASS gold rows for this (ns, scenario, run_date): baseline=[['1']] converted=[['1']]
PASS silver rows for this (ns, scenario): baseline=[['0']] converted=[['0']]
```

## Planted orphan set (the expected answer)

25 objects, 854277 bytes total,
deterministic per namespace:

| bucket | key | size_bytes |
|---|---|---|
| `otterworks-file-storage` | `files/07462a9b-ea16-4563-bcec-9ba1f6c208eb/c28d78ea-dd33-4f7b-ba7b-869655f93e0e` | 39875 |
| `otterworks-file-storage` | `files/08e492d0-b0af-4f29-ab3e-f810f02811c3/c91401bc-22cd-4d4f-8b05-fa6cfcee781e` | 14500 |
| `otterworks-file-storage` | `files/126b032a-407a-490e-b385-9e6c7b410162/d44229e1-65f9-4a31-ad3f-df491a5ecdf9` | 10240 |
| `otterworks-file-storage` | `files/1c074841-4ec3-40f8-8adb-883ae0cf4890/f768950d-fbd0-4175-a675-75084cfa65a2` | 32395 |
| `otterworks-file-storage` | `files/44b10749-def8-4f1d-a551-ab676cb9e8cc/0c5d307d-8572-448b-87eb-af8d26e029ce` | 43724 |
| `otterworks-file-storage` | `files/45b4af51-b471-465b-bf5d-8f0fa6fdbe99/24b81213-d20d-40ae-81e4-51a65582dfc3` | 51180 |
| `otterworks-file-storage` | `files/4ebba49b-182c-45d5-822f-8a4051fc1999/a1e44d50-9ad9-4c13-83f5-f27dfd27c4e8` | 14402 |
| `otterworks-file-storage` | `files/4fc4e65c-d2c3-4035-928d-764ac947b2fa/1c382f81-c917-4afe-aec7-24837b5e8943` | 9066 |
| `otterworks-file-storage` | `files/54b1a903-d814-4234-ba29-37fbe7235d93/372276da-a957-44ef-91f7-71c819226c15` | 25088 |
| `otterworks-file-storage` | `files/7387cd4a-a480-4ff5-874e-4a9b37dd5561/c4ebf31b-85d7-465d-9bb4-a8b8cef5ed84` | 5008 |
| `otterworks-file-storage` | `files/78df8b63-1b32-43d1-9dca-03e19694e925/0776f99d-75c5-4410-a6e0-a0c67a158d1d` | 32221 |
| `otterworks-file-storage` | `files/852285d9-1c8a-49c1-9379-ea950915ea7e/44a1466d-d719-481d-ae6b-b98f12dd9072` | 37868 |
| `otterworks-file-storage` | `files/8a480cf2-d86f-4115-a6a9-b14e627e4036/4299ea15-12f5-4144-a8f6-50396efda02b` | 45623 |
| `otterworks-file-storage` | `files/90cbe207-12dd-4253-b3b9-a5bfcbfc190c/1bc68d16-fe3f-413d-a2b2-f747d11b7ffd` | 34351 |
| `otterworks-file-storage` | `files/9b259a56-0bc4-43b4-b858-223cac2ee2bb/25cd01a5-825a-43ac-926a-ad0f82e4388b` | 13957 |
| `otterworks-file-storage` | `files/a0bec862-f989-43f6-8106-9431a5780364/88efc055-c295-4d63-834e-ecf1e7e9db28` | 24850 |
| `otterworks-file-storage` | `files/a1253df1-7131-480b-bf82-b8f6df678f64/f93296fe-29fd-4f79-a7cf-fcdb6993934f` | 58839 |
| `otterworks-file-storage` | `files/ab421758-0599-43de-b3f7-c54eedea9f2e/fdebdbe1-c10f-4201-8f11-00f6f80a9bc8` | 60112 |
| `otterworks-file-storage` | `files/c223a33b-fd06-4b49-a33a-c7e8028603fe/1f73b4c1-dae9-4148-b7c5-5a6bc23a0b01` | 46717 |
| `otterworks-file-storage` | `files/cccf6ab3-8fda-449c-9f8a-f754134a6842/c0fd0cbb-534b-414c-a2f1-fa08a60a2ed6` | 29478 |
| `otterworks-file-storage` | `files/d133be60-b7e4-4d1e-9f76-03e2dae5ac50/2244cb36-5175-409c-855a-652f36266c81` | 61753 |
| `otterworks-file-storage` | `files/d940b7f1-1d6e-4e1d-ba91-c727e5f712cb/6f8dcfb4-d016-40cc-a4d9-aacaf77750c8` | 51626 |
| `otterworks-file-storage` | `files/ee8f718d-58a6-469a-a718-48ea67c5f522/84f46cba-fd42-4d35-8dcc-e96eddf759b7` | 41271 |
| `otterworks-file-storage` | `files/f2c7d3ea-5480-400c-a0f4-ece22569f630/b90a49ab-9bc1-4e4c-8a63-d622b5e25585` | 41682 |
| `otterworks-file-storage` | `files/fd81181d-b218-44b8-86e1-6fb8608d9c0c/9cdb02c9-e206-4e14-b137-ea740a3783ac` | 28451 |

## Reproducing

```bash
make infra-up && make seed-legacy NS=demo && make seed-legacy-validate NS=demo
python3 scripts/tp_databricks/fixture_storage_cleanup.py build --ns demo
python3 scripts/tp_databricks/extract_storage_cleanup.py --ns demo --load --scenario nominal
python3 scripts/tp_databricks/recon_storage_cleanup.py --ns demo --run-date 2026-08-15 \
    --capture-golden
```
