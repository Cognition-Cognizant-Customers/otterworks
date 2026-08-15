# Contract — `mongo-invoices`: Oracle `INVOICE_HEADER` + `INVOICE_LINE` → Atlas `invoices`

Read `mongo-README.md` in this directory first: it holds the shared rules, checksum
definitions and source connection details that this contract depends on.

## Source

- Oracle `OW_BILLING.INVOICE_HEADER` (`INVOICE_ID` PK, `INVOICE_NO`, `CUST_ID`,
  `TENANT_ID`, `INVOICE_DT` / `DUE_DT` as `DD-MON-YY` strings, `STATUS_CD` →
  `CODES('INV_STATUS')`, `TOTAL_AMT`, `BATCH_NO`).
- Oracle `OW_BILLING.INVOICE_LINE` — the bulk mainframe-conversion feed: no FKs,
  customer fields (`CUST_NO`, `CUST_NAME`) denormalized onto every row,
  `INVOICE_ID` an unenforced pointer at the header, `INVOICE_DT` as text,
  `SERVICE_PERIOD` as `'MMYYYY-MMYYYY'` text, `GL_ACCT_CSV` a comma-separated
  GL split list, `POSTED_YN` a `CHAR(1)` that is sometimes `NULL`.
- This is the denormalized estate, **not** the transactional `INVOICES` /
  `INVOICE_LINES` tables used by the PL/SQL packages — do not migrate those.
- Scope this run to `BATCH_NO = 85559852` (`NS=demo`).

## Target — `ow_tp_demo.invoices` (+ `invoice_lines_orphaned`)

Lines are **embedded** in their header — bounded fan-out (0–23 lines per invoice,
~8 average at demo scale: the seeder assigns each line to a uniformly random
header, so the distribution is Poisson-like, 268 headers carry fewer than 3 lines
and 5 carry none at all), and the app's read pattern is "fetch an invoice with its
lines", so this is a single-document read:

```js
{
  _id: "<INVOICE_ID>",
  invoiceNo: "<INVOICE_NO>",
  customerId: "<CUST_ID>",              // reference to customers._id (no join enforced)
  tenantId: "<TENANT_ID>",
  status: "issued",                     // STATUS_CD → CODES('INV_STATUS')
  invoiceDate: ISODate, dueDate: ISODate,
  totalAmount: NumberDecimal("1234.56"),
  lineCount: 8,
  lineTotal: NumberDecimal("1230.00"),  // sum of embedded line amounts
  lines: [
    { lineId, lineNo, type, description,
      qty: NumberDecimal, unitPrice: NumberDecimal,
      amount: NumberDecimal, taxAmount: NumberDecimal,
      servicePeriod: { from: ISODate, to: ISODate },   // parsed MMYYYY-MMYYYY
      posted: true,                                   // POSTED_YN, absent when NULL
      glAccounts: [40001, 40237],                     // GL_ACCT_CSV → array
      srcSystem: "MAINFRAME" }
  ],
  legacy: { batchNo: 85559852 },
  _migration: { ns: "demo", sourceTable: "OW_BILLING.INVOICE_HEADER", migratedAt: ISODate }
}
```

Modeling rules:
- Monetary values use `NumberDecimal` (BSON Decimal128), never binary doubles.
- Denormalized per-line customer copies (`CUST_NO`, `CUST_NAME`) are **dropped**
  from the line — the header carries `customerId`. Report any line whose
  `CUST_ID` disagrees with its header's as a data-quality finding (this is
  extra signal, not a planted anomaly; keep it separate in the report).
- Lines whose `INVOICE_ID` has no header row are **orphans**: they go to
  `invoice_lines_orphaned` with their raw fields plus
  `quarantine_reason: "missing_header"`, never dropped.
- A header with no matching lines is still migrated: `lines: []`, `lineCount: 0`,
  `lineTotal: NumberDecimal("0.00")`. It is **not** an anomaly and must not be
  quarantined — only lines whose `INVOICE_ID` points at a non-existent header are
  (37 lines over 37 distinct ghost `INVOICE_ID`s).
- `totalAmount` (header) and `lineTotal` (sum of lines) intentionally disagree in
  the source estate; keep both and do not "fix" either.
- Indexes (PR 1): `{ customerId: 1, invoiceDate: -1 }`, `{ invoiceNo: 1 }`
  (unique), `{ tenantId: 1, status: 1 }`, and `{ "lines.lineId": 1 }`.

## Expected results (must match exactly)

| Metric | Expected |
|---|---|
| `invoices` documents | **18,750** |
| Invoices with zero lines (`lines: []`, `lineCount: 0`, `lineTotal` zero) | **5** |
| Invoices with fewer than 3 lines | **268** |
| Embedded lines across all invoices | **149,963** |
| `invoice_lines_orphaned` documents | **37** |
| Embedded + orphaned lines | **150,000** |
| Source-parity checksum over all lines | **`88a66751f0b08b476b492105a2efc537`** |

Checksum recomputation from Atlas (ordered md5, see README): take **every** line
from `invoices.lines` **plus every** document in `invoice_lines_orphaned`, sort by
`lineId` ascending, and feed `f"{lineId}:{amount:.2f}"` + `"\n"` into a single
`md5`. The orphans are part of the source set — excluding them cannot match.
`INVOICE_HEADER` has a manifest row count (18,750) but no checksum; assert the
count.

## Planted anomalies this workload must detect and report

| Kind | Manifest target | Count |
|---|---|---|
| `orphaned_rows` | `oracle.OW_BILLING.INVOICE_LINE` | **37** |

Report exactly **37** orphaned lines, with their `LINE_ID`s and the dangling
`INVOICE_ID`s, and show that all 37 landed in `invoice_lines_orphaned`.

## Deliverable — 3-PR stack into the working branch

1. Workload infra: indexes / collection setup for `invoices` and
   `invoice_lines_orphaned` (never touch `infrastructure/terraform-atlas/`).
2. `migrations/mongodb/invoices/` — extractor (header + lines, streamed and
   batched; do not hold 150k lines in memory at once), transformer (pure,
   unit-tested: service-period parsing, GL CSV split, `NULL` `POSTED_YN`,
   orphan routing), loader (idempotent upsert by `_id`).
3. Recon: script plus committed output — counts, embedded-vs-orphan split,
   checksum comparison against the manifest, and the anomaly ledger.
