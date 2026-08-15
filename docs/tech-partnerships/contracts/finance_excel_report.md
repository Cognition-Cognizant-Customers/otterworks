# Contract: `finance_excel_report.pl` → `ow_tp_finance_report`

Read [README.md](README.md) first — the shared rules there are part of this contract.

| | |
|---|---|
| Source | `etl/legacy-extra/jobs/finance_excel_report.pl` |
| Language / vintage | Perl 5.005-style, no modules, 2004 |
| Legacy schedule | daily 02:10 (overlaps `analytics_daily`) |
| Converted job | `ow_tp_finance_report` (`infrastructure/terraform-databricks/jobs_finance_report.tf`) |
| Position | gold — depends on `ow_tp.silver.custbill_records` |

## Deficiencies this conversion must retire

- "Excel" report is a CSV renamed `.xls` → a real, typed artifact (the gold table is the
  system of record; any file emitted must be a valid file of its stated format).
- Delivery is a `sendmail` pipe that silently no-ops on modern hosts → delivery must be
  explicit and verifiable; if no mail transport exists, the job records a delivery status
  rather than pretending to send.
- Bounced/stale hardcoded recipients (`jake@…`, gone since 2020) → managed distribution
  list in config/secret scope, not in code.
- No `use strict`, no modules, untestable → maintained code with the aggregation expressed
  as SQL against silver.
- Overlapping schedule with `analytics_daily`, lock file never removed, blanket error
  suppression → orchestrated dependency, `max_concurrent_runs = 1`, failures surface.

## Target

| Object | Contents |
|---|---|
| `ow_tp.gold.finance_billing_summary` | `ns`, `currency`, `record_type` (`INVOICE`/`CREDIT`), `record_count BIGINT`, `total_amount DECIMAL(18,2)`, `report_date`, `generated_at`. Aggregated in SQL from `silver.custbill_records`; no in-memory row loop. |
| `ow_tp.gold.finance_report_delivery` | `ns`, `report_date`, `artifact_path`, `recipient_list`, `delivery_status`, `delivered_at` — the audit the sendmail no-op never produced. |
| `/Volumes/ow_tp/bronze/landing/demo/reports/` | optional emitted artifact; if written, it must be a valid file for its extension. |

## Golden legacy output

`/home/ubuntu/tp-golden/custbill/reports/finance_billing_20260815.csv` (185 bytes, 7 lines,
SHA-256 `c8923a71ab5a2d8048ad06ae91840631c009551e9082755fa4672e034a15627e`; the `.xls` is the
same bytes — that is the defect):

```csv
Currency,RecordType,RecordCount,TotalAmount
EUR,INVOICE,22,101554.41
EUR,CREDIT,6,33375.97
GBP,INVOICE,32,183113.58
GBP,CREDIT,5,28454.59
USD,INVOICE,28,130502.15
USD,CREDIT,7,33390.44
```

Regenerate with `make legacy-etl-gen-data NS=demo`, then `make legacy-etl-run
JOB=sftp_ingest_poll`, `JOB=parse_custbill_fixedwidth`, `JOB=finance_excel_report`.
The date in the filename is the run date; the numbers are deterministic for `NS=demo`.

## Acceptance checks (`scripts/tp_databricks/recon_finance_report.py`)

1. Parse the golden CSV and compare against `gold.finance_billing_summary` for
   `ns='demo'`: 6 rows, exactly the currency × record-type grid above, `record_count` equal
   and `total_amount` equal **to the cent** (exact decimal comparison, no float tolerance).
2. Cross-foot: `SUM(record_count) = 100` and the gold totals equal the sums recomputed
   directly from `silver.custbill_records`, so gold cannot drift from silver.
3. `gold.finance_report_delivery` has one row for the run with a delivery status that
   reflects reality — the legacy job's silent no-op must show as an explicit
   non-delivery, not as success.
4. Assert the emitted artifact (if any) is a valid file of its extension — the converted
   job must not reproduce the CSV-named-`.xls` defect.
5. Re-run; assert gold rows are replaced, not duplicated (idempotency for `(ns, report_date)`).
