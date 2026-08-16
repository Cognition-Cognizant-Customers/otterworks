# MongoDB Atlas migration rollup — run tp-run/mongodb-20260816T175745Z (NS=demo)

Live validation window run by the parent session against Atlas cluster
`otterworks-demo`, run database `ow_tp_mongodb_demo` (+ `_quarantine`).
Recon recomputed from Atlas; every unit was migrated, reconciled, re-migrated,
and reconciled again to prove idempotency by observation. All four live recon
reports validate against the recon schema (`make tp-validate-recon`).

| Workload | Legacy source | Docs migrated | Checksum match | Planted anomalies detected | Live recon | Child session |
|---|---|---|---|---|---|---|
| mongo_customers | Oracle OW_BILLING.CUSTOMER_MASTER (155 cols) + ENTITY_ATTR_VALUE | 25,000 (8,333 EAV folded) | pass (`4f92feef…`) | 31 malformed CSV lists, 50 dirty dates | 6/6 pass, idempotent | [customers](https://partner-workshops.devinenterprise.com/sessions/0787ebffed1348aebaa68547b31c3ee4) |
| mongo_invoices | Oracle INVOICE_HEADER + INVOICE_LINE | 18,750 invoices / 149,963 embedded lines | pass (`88a66751…`, 150,000 line rows) | 37 orphaned invoice lines quarantined | 4/4 pass, idempotent | [invoices](https://partner-workshops.devinenterprise.com/sessions/8a264343e12f4cb18a0188f39e7317b6) |
| mongo_documents | Postgres documents / document_versions / document_snapshots | 2,000 docs / 13,876 embedded versions / 390 snapshots | pass (3 checksums) | 10 version gaps reported, 6 orphaned snapshots quarantined | 7/7 pass, idempotent | [documents](https://partner-workshops.devinenterprise.com/sessions/1bf0262a9247473e81c49ace1108df6d) |
| mongo_files | DynamoDB otterworks-file-metadata | 10,000 | pass (`db614663…`) | 40 orphaned metadata records quarantined | 3/3 pass, idempotent | [files](https://partner-workshops.devinenterprise.com/sessions/acac81345109448db70d9c91672a5f1d) |

## Planted-anomaly set comparison (baseline manifest vs live recon)

Baseline `testdata/legacy/manifests/demo.json` plants: 31 malformed CSV lists,
50 dirty dates, 37 orphaned invoice lines, 6 orphaned Postgres snapshots,
10 version gaps, 40 orphaned DynamoDB metadata records, 1 missing S3 hour.

- Detected by units (exact counts, no extras): 31 + 50 (customers), 37
  (invoices), 6 + 10 (documents), 40 (files).
- Declared coverage gap: `missing_hours` (1 missing S3 hour) — no unit ingests
  S3 events; declared in `contracts/mongo_files.json` as a coverage_gap.

## Unverified paths disclosed by units

- customers: invalid-UTF-8 quarantine path not exercised by the seeded fixture;
  some nullable date columns unpopulated by the demo seed.
- invoices: invalid-UTF-8 and NULL-amount quarantine paths not exercised;
  empty-input semantics tested against an unseeded namespace.
- documents: invalid-byte quarantine path not exercised; full-scale memory
  behavior unverified (M0/demo scale).
- files: `missing_hours` coverage gap (S3 events not ingested).

## Teardown

Run-scoped Atlas state (run/quarantine databases, run database user, run
access-list entry) is dropped and `terraform destroy` is verified negatively
after the live window; see `infrastructure/terraform-atlas/README.md`.
