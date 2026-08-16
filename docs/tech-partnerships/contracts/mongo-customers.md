# Contract — `mongo-customers`: Oracle `CUSTOMER_MASTER` + EAV → Atlas `customers`

Read `mongo-README.md` in this directory first: it holds the shared rules, checksum
definitions and source connection details that this contract depends on.

## Source

- Oracle `OW_BILLING.CUSTOMER_MASTER` — 155 columns of denormalized horror:
  repeating groups (`ADDR_LINE_1..6`, `MAIL_ADDR_LINE_1..6`, `PHONE1..4` with
  parallel `PHONEn_TYPE_CD`), `FLAG_01..20`, `UDF_01..40` / `UDF_AMT_01..10` /
  `UDF_DT_01..10`, dates as `VARCHAR2(9)` `'DD-MON-YY'` strings
  (`SIGNUP_DT`, `LAST_ACTIVITY_DT`, `LAST_INVOICE_DT`, `LAST_PAYMENT_DT`,
  `TERMINATE_DT`), comma-separated id lists in `VARCHAR2`
  (`RELATED_ACCT_IDS`, `CHILD_ACCT_IDS`, `PROMO_CODES_CSV`), magic-number
  statuses (`STATUS_CD`, `SUB_STATUS_CD`, `CUST_TYPE_CD`, `PHONEn_TYPE_CD`)
  resolved through the generic `CODES(code_type, code_value, code_desc)` table,
  and full-row-copy history in `CUSTOMER_MASTER_HIST` maintained by triggers.
- Oracle `OW_BILLING.ENTITY_ATTR_VALUE` — EAV rows
  (`entity_type`, `entity_id`, `attr_name`, `attr_value`, `attr_type`,
  `created_dt` as `DD-MON-YY`); the rows for this workload are
  `entity_type = 'CUSTOMER'` with `entity_id` = `CUST_ID`.
- Scope this run to the namespace's batch: `CONVERSION_BATCH_NO = 85559852`
  (`NS=demo`). Do not migrate `CUSTOMER_MASTER_HIST`.

## Target — `ow_tp_demo.customers`

Document model (sketch; refine idiomatically, keep the shape):

```js
{
  _id: "<CUST_ID>",                       // deterministic: source PK
  tenantId: "<TENANT_ID>",
  customerNo: "<CUST_NO>",
  name: { display, legal, dba },          // CUST_NAME / LEGAL_NAME / DBA_NAME; drop CUST_NAME_UPPER
  addresses: [                            // repeating groups collapse to an array
    { type: "primary", lines: [...], city, state, postalCode, postalCodeExt, country },
    { type: "mailing", lines: [...], city, state, postalCode }
  ],
  phones: [ { type: "billing", number: "..." } ],   // PHONEn + PHONEn_TYPE_CD → CODES('PHONE_TYPE')
  emails: [ ... ],                        // EMAIL_1..3, nulls dropped
  status: "active",                       // STATUS_CD → CODES('CUST_STATUS') label
  subStatusCode, customerType: "business",// CUST_TYPE_CD → CODES('CUST_TYPE')
  classification: { segment, region, territory, channel, rateClass },
  flags: { taxExempt: false, creditHold: false, dunningExempt: false, vip: true },
  balances: { current: 1234.56, pastDue, ytdBilled, ltdBilled, ytdPaid, creditLimit },
  dates: { signup: ISODate, lastActivity: ISODate, ... },   // parsed DD-MON-YY → BSON date
  relatedAccountIds: [...], childAccountIds: [...], promoCodes: [...],  // CSV → arrays
  attributes: { PORTAL_THEME: "blue", Y2K_VERIFIED: true, ... },        // EAV folded in, typed by ATTR_TYPE
  legacy: { sysKey, mainframeAcctNo, conversionBatchNo, rowVersionNo,
            sparse: { UDF_07: "...", FLAG_13: "Y" } },  // only non-null UDF/FLAG values
  _migration: { ns: "demo", sourceTable: "OW_BILLING.CUSTOMER_MASTER", migratedAt: ISODate }
}
```

Modeling rules:
- Sparse columns are **omitted**, never stored as null. Empty repeating-group
  slots do not produce array entries.
- Code lookups resolve to human labels via `CODES`; keep the raw numeric code
  alongside only where the label is not 1:1 (`SUB_STATUS_CD`).
- `Y`/`N` character flags become booleans; `NULL` means absent.
- EAV values are typed by `ATTR_TYPE` (`STR`/`NUM`/`DATE`/`BOOL`); an EAV row
  whose `attr_name` collides with a modelled field goes to `attributes` only.
- The seeded EAV rows are **not** unique per `(ENTITY_ID, ATTR_NAME)`: for this
  batch 8,333 rows collapse onto 8,141 distinct keys across 187 colliding keys.
  Because `attributes` is a name-keyed object, the winner must be deterministic:
  the row with the greatest `CREATED_DT`, tie-broken by the lexicographically
  greatest `ATTR_VALUE`. Losing rows are **preserved, never dropped** — each one
  becomes an entry in `legacy.attributeConflicts: [{ name, value, type, createdAt }]`,
  so 8,141 folded keys + 192 conflict entries account for all 8,333 source rows.
- Indexes for the workload's own collections belong in PR 1 of the stack:
  at minimum `{ tenantId: 1, status: 1 }`, `{ customerNo: 1 }` (unique),
  and `{ "dates.signup": -1 }`.

## Expected results (must match exactly)

| Metric | Expected |
|---|---|
| `customers` documents | **25,000** |
| Source-parity checksum over `customers` | **`4f92feef2ad58dbab30e289957931928`** |
| EAV source rows consumed (`ENTITY_TYPE='CUSTOMER'`) | **8,333** |
| Distinct attribute keys folded into `attributes` | **8,141** |
| Duplicate-key rows preserved under `legacy.attributeConflicts` | **192** |
| Customers carrying at least one attribute | **7,075** |

Checksum recomputation from Atlas (ordered md5, see README): sort documents by
`_id` ascending and feed `f"{_id}:{balances.current:.2f}"` + `"\n"` into a single
`md5`. Quarantined customers are still counted in both the document count and the
checksum: a quarantined *field* does not remove the customer document — the
customer is migrated with the offending field preserved raw under
`_quarantine.<field>` and the parsed field omitted.

## Planted anomalies this workload must detect and report

| Kind | Manifest target | Count |
|---|---|---|
| `dirty_dates` | `oracle.OW_BILLING.CUSTOMER_MASTER.SIGNUP_DT` | **50** |
| `malformed_csv_lists` | `oracle.OW_BILLING.CUSTOMER_MASTER.RELATED_ACCT_IDS` | **31** |

- Dirty dates are values that are not parseable as `DD-MON-YY`
  (e.g. `31-FEB-24`, `N/A`, empty-ish markers). Report the exact count and the
  affected `CUST_ID`s; the recon report must show **50**, no more, no fewer.
- Malformed CSV lists are `RELATED_ACCT_IDS` values that do not parse as a clean
  comma-separated id list (empty elements, trailing separators, non-id tokens).
  Report the exact count (**31**) and keep the raw string on the document.

## Deliverable — 3-PR stack into the working branch

1. Workload infra: index definitions / collection setup for `customers` and
   `customers_quarantine` (Terraform or an idempotent setup script — do **not**
   touch `infrastructure/terraform-atlas/`).
2. `migrations/mongodb/customers/` — extractor (Oracle, batched, `oracledb`),
   transformer (pure, unit-tested against a handful of fixture rows including a
   dirty date and a malformed CSV), loader (idempotent upsert by `_id`).
3. Recon: a runnable recon script plus its committed output showing counts,
   checksum comparison against the manifest, and the anomaly ledger.
