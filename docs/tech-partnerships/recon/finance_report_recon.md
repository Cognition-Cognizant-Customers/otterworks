# Recon: `ow_tp_finance_report` vs `finance_excel_report.pl`

- verdict: **green**
- namespace: `demo`, report_date: `2026-08-15`
- golden legacy artifact: `/tmp/otterworks-legacy/reports/finance_billing_20260815.csv` (185 bytes, sha256 `c8923a71ab5a2d8048ad06ae91840631c009551e9082755fa4672e034a15627e`)
- converted output: `ow_tp.gold.finance_billing_summary`, `ow_tp.gold.finance_report_delivery`
- both sides produced independently: the golden side is the legacy Perl job's own output, regenerated with the `legacy-etl-*` targets; the converted side is the job's statement set executed on the serverless SQL warehouse.

## Compared values

| Currency | RecordType | Legacy count | Gold count | Legacy total | Gold total |
|---|---|---:|---:|---:|---:|
| EUR | INVOICE | 22 | 22 | 101554.41 | 101554.41 |
| EUR | CREDIT | 6 | 6 | 33375.97 | 33375.97 |
| GBP | INVOICE | 32 | 32 | 183113.58 | 183113.58 |
| GBP | CREDIT | 5 | 5 | 28454.59 | 28454.59 |
| USD | INVOICE | 28 | 28 | 130502.15 | 130502.15 |
| USD | CREDIT | 7 | 7 | 33390.44 | 33390.44 |

## Checks

### 1. Row-level parity with the golden legacy report (exact decimals) — **PASS**

- golden rows: 6, gold table rows: 6
- ok: gold.finance_billing_summary has 6 rows (got 6)
- ok: currency x record_type grid matches [('EUR', 'INVOICE'), ('EUR', 'CREDIT'), ('GBP', 'INVOICE'), ('GBP', 'CREDIT'), ('USD', 'INVOICE'), ('USD', 'CREDIT')] (got [('EUR', 'INVOICE'), ('EUR', 'CREDIT'), ('GBP', 'INVOICE'), ('GBP', 'CREDIT'), ('USD', 'INVOICE'), ('USD', 'CREDIT')])
- ok: EUR/INVOICE: count 22 == 22, total 101554.41 == 101554.41
- ok: EUR/CREDIT: count 6 == 6, total 33375.97 == 33375.97
- ok: GBP/INVOICE: count 32 == 32, total 183113.58 == 183113.58
- ok: GBP/CREDIT: count 5 == 5, total 28454.59 == 28454.59
- ok: USD/INVOICE: count 28 == 28, total 130502.15 == 130502.15
- ok: USD/CREDIT: count 7 == 7, total 33390.44 == 33390.44

### 2. Cross-foot: 100 records and gold totals equal silver recomputed — **PASS**

- ok: SUM(record_count) = 100 (got 100)
- ok: EUR/INVOICE: gold (22, 101554.41) == silver (22, Decimal('101554.41'))
- ok: EUR/CREDIT: gold (6, 33375.97) == silver (6, Decimal('33375.97'))
- ok: GBP/INVOICE: gold (32, 183113.58) == silver (32, Decimal('183113.58'))
- ok: GBP/CREDIT: gold (5, 28454.59) == silver (5, Decimal('28454.59'))
- ok: USD/INVOICE: gold (28, 130502.15) == silver (28, Decimal('130502.15'))
- ok: USD/CREDIT: gold (7, 33390.44) == silver (7, Decimal('33390.44'))
- ok: silver detail rows aggregated = 100 (got 100)

### 3. Delivery audit row tells the truth about delivery — **PASS**

- ok: exactly one delivery row for the run (got 1)
- status=NOT_DELIVERED_NO_TRANSPORT_CONFIGURED recipients_configured=True recipient_count=2 artifact=/Volumes/ow_tp/bronze/landing/demo/reports/finance_billing_20260815.csv
- ok: delivery_status is a known value (got 'NOT_DELIVERED_NO_TRANSPORT_CONFIGURED')
- ok: non-delivery is not stamped as delivered (delivered_at=None)
- ok: the sendmail no-op is recorded as an explicit non-delivery, not as success
- ok: recipient list resolved from the secret scope, not from code

### 4. Emitted artifact is a valid file of its extension — **PASS**

- /Volumes/ow_tp/bronze/landing/demo/reports: ['finance_billing_20260815.csv']
- UNVERIFIED: the documented `/Volumes/ow_tp/bronze/landing` upload via `dbx.py upload` could not be verified because the API returned `{"error_code":403,"message":"Provided access token does not have required scopes: files"}`.
- The in-Databricks landing used for this run is a bootstrap workaround, not the production transport.
- ok: no .xls artifact (the legacy CSV-renamed-.xls defect is gone)
- ok: finance_billing_20260815.csv present
- ok: finance_billing_20260815.csv is not empty (got 7 lines)
- ok: CSV header well formed (got ['Currency', 'RecordType', 'RecordCount', 'TotalAmount'])
- ok: every CSV line has 4 fields (parses as CSV, is not a renamed foreign format)
- ok: artifact body equals the golden report rows (got [('EUR', 'INVOICE', 22, Decimal('101554.41')), ('EUR', 'CREDIT', 6, Decimal('33375.97')), ('GBP', 'INVOICE', 32, Decimal('183113.58')), ('GBP', 'CREDIT', 5, Decimal('28454.59')), ('USD', 'INVOICE', 28, Decimal('130502.15')), ('USD', 'CREDIT', 7, Decimal('33390.44'))])

### 5. Idempotency: replaying summary and delivery statements avoids duplicates — **PASS**

- re-executing the job's 2 summary statements and delivery statements against the serverless warehouse
- `DELETE FROM ow_tp.gold.finance_billing_summary WHERE ns = 'd...` -> [['6']]
- `INSERT INTO ow_tp.gold.finance_billing_summary (ns, currency...` -> [['6', '6']]
- ok: still 6 gold rows after the re-run (got 6)
- ok: counts and totals unchanged by the summary re-run
- re-executing the job's 1 delivery statements using the values already stored in the audit row
- `MERGE INTO ow_tp.gold.finance_report_delivery AS target USIN...` -> [['1', '1', '0', '0']]
- ok: still exactly one delivery row after replay (got 1)
- ok: delivery replay preserves the audit values
- ok: delivery replay preserves delivered_at (None)
