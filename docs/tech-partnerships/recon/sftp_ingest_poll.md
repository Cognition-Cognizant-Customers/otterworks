# Recon: `sftp_ingest_poll.ksh` → `ow_tp_sftp_ingest`

- Generated: 2026-08-16T00:37:31+00:00
- Namespace: `demo`  |  catalog: `ow_tp`  |  landing: `/Volumes/ow_tp/bronze/landing`
- Golden baseline provenance: artifacts of a real `sftp_ingest_poll.ksh` run (`make legacy-etl-gen-data NS=demo` + `make legacy-etl-run JOB=sftp_ingest_poll`), read byte-for-byte from `/home/ubuntu/tp-golden/custbill/incoming/*.dat.done`
- Result: **green**

| # | Check | Result |
|---|---|---|
| 1 | bronze.custbill_files matches the golden artifacts | **PASS** |
| 2 | bronze.custbill_lines preserves the raw records | **PASS** |
| 3 | TRL-declared count equals detail lines ingested | **PASS** |
| 4 | re-running the ingest leaves both tables byte-identical | **PASS** |
| 5 | no ow_tp object outside the contract, no unprefixed object | **PASS** |
| 6 | a half-written file neither lands nor blocks the complete files | **PASS** |

## 1. bronze.custbill_files matches the golden artifacts — PASS

```
row count: converted=2 golden=2
CUSTBILL_DEMO_001.dat: size_bytes converted=3430 golden=3430 [ok]
CUSTBILL_DEMO_001.dat: sha256 converted=c70f30ca08842885fe2bc96c3902d463609d05be95a96c01875b825a88aa336c golden=c70f30ca08842885fe2bc96c3902d463609d05be95a96c01875b825a88aa336c [ok]
CUSTBILL_DEMO_001.dat: record_count converted=52 golden=52 [ok]
CUSTBILL_DEMO_001.dat: source_path converted=dbfs:/Volumes/ow_tp/bronze/landing/demo/custbill/CUSTBILL_DEMO_001.dat expected=/Volumes/ow_tp/bronze/landing/demo/custbill/CUSTBILL_DEMO_001.dat [ok]
CUSTBILL_DEMO_002.dat: size_bytes converted=3430 golden=3430 [ok]
CUSTBILL_DEMO_002.dat: sha256 converted=652974b8bb3a168483c8f63fb9f2db440a6ff606f4563cd0800f15914b344f48 golden=652974b8bb3a168483c8f63fb9f2db440a6ff606f4563cd0800f15914b344f48 [ok]
CUSTBILL_DEMO_002.dat: record_count converted=52 golden=52 [ok]
CUSTBILL_DEMO_002.dat: source_path converted=dbfs:/Volumes/ow_tp/bronze/landing/demo/custbill/CUSTBILL_DEMO_002.dat expected=/Volumes/ow_tp/bronze/landing/demo/custbill/CUSTBILL_DEMO_002.dat [ok]
```

## 2. bronze.custbill_lines preserves the raw records — PASS

```
total rows: converted=104 golden=104
CUSTBILL_DEMO_001.dat: lines converted=52 golden=52 [ok]
CUSTBILL_DEMO_001.dat: record lengths converted=[63, 65] golden=[63, 65] [ok]
CUSTBILL_DEMO_001.dat: all 52 raw_line values byte-identical to golden (HDR/TRL included)
CUSTBILL_DEMO_002.dat: lines converted=52 golden=52 [ok]
CUSTBILL_DEMO_002.dat: record lengths converted=[63, 65] golden=[63, 65] [ok]
CUSTBILL_DEMO_002.dat: all 52 raw_line values byte-identical to golden (HDR/TRL included)
```

## 3. TRL-declared count equals detail lines ingested — PASS

```
CUSTBILL_DEMO_001.dat: TRL declared=50 ingested detail lines=50 golden detail lines=50 [ok]
CUSTBILL_DEMO_002.dat: TRL declared=50 ingested detail lines=50 golden detail lines=50 [ok]
```

## 4. re-running the ingest leaves both tables byte-identical — PASS

```
files the re-run reads under /Volumes/ow_tp/bronze/landing/demo/custbill/: ['CUSTBILL_DEMO_001.dat', 'CUSTBILL_DEMO_002.dat'] (golden: ['CUSTBILL_DEMO_001.dat', 'CUSTBILL_DEMO_002.dat'])
custbill_files: rows:sha256 before=2:ccd3f02d800f3c58514308fb75dbdd8b13782dd76d28c2b089e604da6c24483d after=2:ccd3f02d800f3c58514308fb75dbdd8b13782dd76d28c2b089e604da6c24483d [unchanged]
custbill_lines: rows:sha256 before=104:88e7249ece103b7bec93e4964f8b8a39ee429e410e3b09ff180fbf0435b826c0 after=104:88e7249ece103b7bec93e4964f8b8a39ee429e410e3b09ff180fbf0435b826c0 [unchanged]
```

## 5. no ow_tp object outside the contract, no unprefixed object — PASS

```
statements analyzed for landing_root=/Volumes/ow_tp/bronze/landing
tables referenced by the statement set: ['ow_tp.bronze.custbill_files', 'ow_tp.bronze.custbill_lines']
outside the contract: none
unprefixed: none
write targets in the statement set: ['ow_tp.bronze.custbill_files', 'ow_tp.bronze.custbill_lines']
write targets outside the contract: none
retention SQL targets: ['ow_tp.bronze.custbill_files', 'ow_tp.bronze.custbill_lines'] outside=none
contracted tables present: ['ow_tp.bronze.custbill_files', 'ow_tp.bronze.custbill_lines'] missing=none
other ow_tp tables in the catalog (other units', not written by this unit per (a)): ['ow_tp.bronze.analytics_daily_stage', 'ow_tp.bronze.analytics_events_raw', 'ow_tp.bronze.audit_events_raw', 'ow_tp.bronze.custbill_raw_lines_bootstrap', 'ow_tp.bronze.file_metadata_raw', 'ow_tp.bronze.search_documents_raw', 'ow_tp.bronze.storage_extract_manifest', 'ow_tp.bronze.storage_objects_raw', 'ow_tp.bronze.user_activity_events_landed', 'ow_tp.bronze.user_activity_raw', 'ow_tp.bronze.user_activity_upstream_fixture', 'ow_tp.gold.analytics_daily_summary', 'ow_tp.gold.audit_archive_manifest', 'ow_tp.gold.finance_billing_summary', 'ow_tp.gold.finance_report_delivery', 'ow_tp.gold.search_reindex_summary', 'ow_tp.gold.storage_cleanup_savings', 'ow_tp.gold.user_activity_report', 'ow_tp.gold.user_activity_run_log', 'ow_tp.silver.analytics_events', 'ow_tp.silver.analytics_events_rejects', 'ow_tp.silver.audit_events_archived', 'ow_tp.silver.custbill_file_recon', 'ow_tp.silver.custbill_file_recon_staging', 'ow_tp.silver.custbill_records', 'ow_tp.silver.custbill_records_staging', 'ow_tp.silver.custbill_rejects', 'ow_tp.silver.custbill_rejects_staging', 'ow_tp.silver.search_index_documents', 'ow_tp.silver.search_index_documents_staging', 'ow_tp.silver.storage_orphans', 'ow_tp.silver.user_activity_daily']
namespaces present in bronze.custbill_lines: ['demo', 'trlneg2'] (this unit only ever writes ns='demo'; other namespaces are other runs')
ow_tp jobs in the workspace: none (1/3 not applied yet)
this unit's throwaway ow_tp_dev_sftp_ingest left behind: none
other units' ow_tp_dev_* jobs (not this unit's, not judged): none
catalogs=['ow_tp'] secret_scopes=['ow_tp'] dirs=['/Shared/ow_tp']
```

## 6. a half-written file neither lands nor blocks the complete files — PASS

```
fixtures under /Volumes/ow_tp/bronze/landing/gateprobe/custbill/: ['CUSTBILL_GATE_GOOD.dat', 'CUSTBILL_GATE_PARTIAL.dat']
the gate calls incomplete: ['CUSTBILL_GATE_PARTIAL.dat']; complete: ['CUSTBILL_GATE_GOOD.dat']
observed vs declared: CUSTBILL_GATE_PARTIAL.dat (observed 1450 bytes, hdr=1, trl=1, detail=20; TRL declares 50)
run over the mixed drop: failed, as required: completeness handshake failed; these files were NOT ingested and remain in the drop path: CUSTBILL_GATE_PARTIAL.dat (observed 1450 bytes, hdr=1, trl=1, detail=20; TRL declares 50). Ingested this run: ['CUSTBILL_GATE_GOOD.dat']
error names every refused file ['CUSTBILL_GATE_PARTIAL.dat']: ok
manifest rows after the run: ['CUSTBILL_GATE_GOOD.dat'] expected=['CUSTBILL_GATE_GOOD.dat'] [ok]
files with raw lines after the run: ['CUSTBILL_GATE_GOOD.dat'] expected=['CUSTBILL_GATE_GOOD.dat'] [ok]
CUSTBILL_GATE_PARTIAL.dat: manifest rows=0 lines=0 [ok]
CUSTBILL_GATE_GOOD.dat: ingested whole — hdr=1 trl=1 detail=50 TRL declares=50 [ok]
drop path after the run: ['CUSTBILL_GATE_GOOD.dat', 'CUSTBILL_GATE_PARTIAL.dat'] expected=['CUSTBILL_GATE_GOOD.dat', 'CUSTBILL_GATE_PARTIAL.dat'] [ok]
probe rows removed again: ok
```

## Scope and caveats

* **Retention is row-only, landing is the archive.** The `retention` task trims rows from
  `bronze.custbill_files` / `bronze.custbill_lines` past `retention_days`; it never removes the
  landed drop file. That mirrors the legacy job, which renamed each drop to `*.done` in place and
  kept it forever — those `.done` files are exactly the golden artifacts hashed above. Landing
  therefore stays the replay source, and a trimmed file re-ingests on a later run; that is
  intended, not a leak. Re-ingest cannot duplicate, because the manifest is keyed on
  `(ns, file_name)` and carries the whole-file `sha256` (see check 4).
* **A half-written drop fails the run, after the complete files have landed.** Check 6 above
  exercises that on a real mixed drop: the complete file lands in both tables, the truncated one
  contributes no row and stays in the drop path unconsumed, and the run still exits non-zero naming
  it with the bytes observed against the count its trailer declares. The gate is content-based, with
  no grace window and no timeout — that heuristic is what this conversion replaces. Check 6's
  fixtures live in their own namespace and its rows are deleted again, so the reconciled namespace
  above is untouched by it.
* **`make dbx-upload` is UNVERIFIED.** The documented upload transport was attempted on this run
  and refused:

  ```
  $ make dbx-upload NS=demo
  PUT /api/2.0/fs/files/Volumes/ow_tp/bronze/landing/demo/_upload_probe/upload_probe.txt
  -> DatabricksError: PUT /api/2.0/fs/files/Volumes/ow_tp/bronze/landing/demo/_upload_probe/upload_probe.txt?overwrite=true -> 403: {"error_code":403,"message":"Provided access token does not have required scopes: files [ReqId: a8cbbc61-b45e-4eac-af07-37455a4555e5]"}
  ```

  The inputs the checks above read were landed inside Databricks instead (serverless task writing
  to the volume). That is a demo workaround, **not** the production transport, and no check was
  weakened to accommodate it — every assertion still reads what is actually in the volume and in
  the tables, compared against the golden `.done` artifacts on disk.

Reproduce with:

```bash
export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
python3 scripts/tp_databricks/recon_sftp_ingest.py --ns demo --report docs/tech-partnerships/recon/sftp_ingest_poll.md
```
