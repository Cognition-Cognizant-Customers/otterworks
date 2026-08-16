# MongoDB migration units

One script pair per workload unit (contract: `docs/tech-partnerships/contracts/`).
Each unit reads its legacy source, writes namespaced target collections, and ships a
recon generator that recomputes counts/checksums **from the target** MongoDB.

Conventions:

- Namespaced targets: databases `ow_tp_mongodb_<ns>` and `ow_tp_mongodb_<ns>_quarantine`.
- Deterministic ids: documents are keyed on the source primary keys (already
  deterministic md5-uuids from the seeders); any derived id uses `uuid5`, never `uuid4`.
- Idempotent: reruns upsert on `_id` and prune only ids owned by the same namespace
  batch that vanished from the source; identical recon numbers on rerun.
- Empty/absent source namespace: strict no-op — nothing is written, prior target
  output is left untouched.
- Connection: `MONGODB_ATLAS_URI` (falls back to `MONGODB_URI`, then the local
  fixture `mongodb://localhost:27777/?directConnection=true`). Never commit or print credential values.

## mongo_invoices

- `make tp-mongo-migrate-invoices NS=<ns>` — Oracle `OW_BILLING.INVOICE_HEADER` +
  `INVOICE_LINE` → `ow_tp_mongodb_<ns>.invoices` (lines embedded in their header);
  orphaned lines (no matching header) → `ow_tp_mongodb_<ns>_quarantine.invoice_lines_quarantine`
  with attribution, never dropped and never embedded under a fabricated header.
- `make tp-mongo-recon-invoices NS=<ns>` — recomputes counts and the ordered
  PK+amount md5 checksum from the target collections, diffs against
  `testdata/legacy/manifests/<ns>.json`, and writes
  `docs/tech-partnerships/recon/mongo_invoices.<ns>.recon.json`
  (validate with `make tp-validate-recon FILE=<report>`).
