# Recon: `sftp_ingest_poll.ksh` → `ow_tp_sftp_ingest`

- Generated: 2026-08-15T22:29:38+00:00
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

## 1. bronze.custbill_files matches the golden artifacts — PASS

```
row count: converted=2 golden=2
CUSTBILL_DEMO_001.dat: size_bytes converted=3430 golden=3430 [ok]
CUSTBILL_DEMO_001.dat: sha256 converted=c70f30ca08842885fe2bc96c3902d463609d05be95a96c01875b825a88aa336c golden=c70f30ca08842885fe2bc96c3902d463609d05be95a96c01875b825a88aa336c [ok]
CUSTBILL_DEMO_002.dat: size_bytes converted=3430 golden=3430 [ok]
CUSTBILL_DEMO_002.dat: sha256 converted=652974b8bb3a168483c8f63fb9f2db440a6ff606f4563cd0800f15914b344f48 golden=652974b8bb3a168483c8f63fb9f2db440a6ff606f4563cd0800f15914b344f48 [ok]
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
custbill_files: rows:sha256 before=2:ccd3f02d800f3c58514308fb75dbdd8b13782dd76d28c2b089e604da6c24483d after=2:ccd3f02d800f3c58514308fb75dbdd8b13782dd76d28c2b089e604da6c24483d [unchanged]
custbill_lines: rows:sha256 before=104:88e7249ece103b7bec93e4964f8b8a39ee429e410e3b09ff180fbf0435b826c0 after=104:88e7249ece103b7bec93e4964f8b8a39ee429e410e3b09ff180fbf0435b826c0 [unchanged]
```

## 5. no ow_tp object outside the contract, no unprefixed object — PASS

```
tables referenced by the statement set: ['ow_tp.bronze.custbill_files', 'ow_tp.bronze.custbill_lines']
outside the contract: none
unprefixed: none
retention SQL targets: ['ow_tp.bronze.custbill_files', 'ow_tp.bronze.custbill_lines'] outside=none
contracted tables present: ['ow_tp.bronze.custbill_files', 'ow_tp.bronze.custbill_lines'] missing=none
other ow_tp tables in the catalog (other units', not written by this unit per (a)): ['ow_tp.bronze.audit_events_raw', 'ow_tp.bronze.custbill_raw_lines_bootstrap', 'ow_tp.bronze.file_metadata_raw', 'ow_tp.bronze.search_documents_raw', 'ow_tp.bronze.storage_extract_manifest', 'ow_tp.bronze.storage_objects_raw', 'ow_tp.gold.audit_archive_manifest', 'ow_tp.gold.finance_billing_summary', 'ow_tp.gold.finance_report_delivery', 'ow_tp.gold.search_reindex_summary', 'ow_tp.gold.storage_cleanup_savings', 'ow_tp.silver.audit_events_archived', 'ow_tp.silver.custbill_file_recon', 'ow_tp.silver.custbill_records', 'ow_tp.silver.custbill_rejects', 'ow_tp.silver.search_index_documents', 'ow_tp.silver.search_index_documents_staging', 'ow_tp.silver.storage_orphans']
ow_tp jobs in the workspace: none (1/3 not applied yet)
throwaway ow_tp_dev_* jobs left behind: none
catalogs=['ow_tp'] secret_scopes=['ow_tp'] dirs=['/Shared/ow_tp']
```

Reproduce with:

```bash
export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
python3 scripts/tp_databricks/recon_sftp_ingest.py --ns demo --report docs/tech-partnerships/recon/sftp_ingest_poll.md
```
