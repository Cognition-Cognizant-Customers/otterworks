# MongoDB migration workload contracts (NS=demo, SCALE=demo)

One contract file per migration workload. Each is the acceptance contract for a
single child session: source store and schema, target Atlas collection and
document model, the exact expected counts and checksums from the seed manifest,
and the planted anomalies that migration must surface.

The before-contract is `testdata/legacy/manifests/<ns>.json`, written by the
deterministic seeders (`make oracle-billing-seed NS=demo`,
`make seed-legacy NS=demo`). It is a runtime artifact (gitignored); the numbers
quoted in the contracts are the `NS=demo`, `SCALE=demo` values, which are
reproducible because the RNG seed derives from the namespace
(`seed = 714559852`, `generated_at = 2026-08-01T00:00:00Z`).

## Shared rules for every workload

- **Target**: Atlas M0 cluster `otterworks-demo`, database `ow_tp_demo`. The
  cluster, the migration DB user and the project IP access-list entry are owned
  by the parent session's Terraform stack under `infrastructure/terraform-atlas/`.
  A workload never runs that stack and never writes outside its own collections.
- **Migration code** lives under `migrations/mongodb/<workload>/` as
  extractor → transformer → loader, parameterized by `NS`, idempotent: rerunning
  for the same namespace must reproduce identical collection contents, counts and
  checksums (upsert by deterministic `_id`, never blind insert).
- **Recon** recomputes counts and checksums *from Atlas* and diffs them against
  the manifest. A recon run that matches counts but does not enumerate the
  planted anomalies is a failure, not a pass.
- **Quarantine**: rows that cannot be faithfully modelled (unparseable dates,
  malformed CSV lists, orphaned children) are written to a
  `<collection>_quarantine` collection with the raw source values and a
  `quarantine_reason`, and are counted in the recon report. Quarantined rows
  still belong to the source-parity checksum unless a contract says otherwise.
- **Branching / PRs**: work off `tech-partnerships`, never `main`, never merge or
  copy from `tech-partnerships-solutions`. Deliver a 3-PR stack (infra/indexes →
  migration code → recon report + evidence), each PR green on `make tp-smoke`
  with the golden path (`make up` / `make test`) untouched.

## Checksum definitions (must be reproduced exactly)

Two different checksum schemes exist in the manifest; use the one that matches
the target.

**Oracle targets — ordered md5.** `md5` over the concatenation of
`f"{pk}:{amount}\n"` for every row, fed in **ascending `pk` string order**,
where `amount` is the 2-decimal string form of the amount column:

| Manifest target | pk | amount |
|---|---|---|
| `oracle.OW_BILLING.CUSTOMER_MASTER` | `CUST_ID` | `CUR_BAL_AMT` (`f"{v:.2f}"`) |
| `oracle.OW_BILLING.INVOICE_LINE` | `LINE_ID` | `AMOUNT` (`f"{v:.2f}"`) |

**Postgres / DynamoDB targets — order-independent sum of md5.** Sum the md5
digests of every line as 128-bit big-endian integers, modulo 2^128, rendered as
32 lowercase hex chars (see `legacy_common.Checksum`). Line formats:

| Manifest target | Line |
|---|---|
| `postgres.otterworks_demo.documents` | `{id}\|{version}\|{word_count}` |
| `postgres.otterworks_demo.document_versions` | `{document_id}\|{version_number}` |
| `postgres.otterworks_demo.document_snapshots` | `{id}\|{document_id}` |
| `dynamodb.file-metadata` | `{id}\|{size_bytes}\|{s3_key}` |

## Source connection details (local before-state, already running)

| Store | Connection |
|---|---|
| Oracle `OW_BILLING` | `ow_billing/ow_billing@localhost:52521/FREEPDB1` (PDB `FREEPDB1`) |
| Postgres | `postgresql://otterworks:otterworks_dev@localhost:5432/otterworks`, schema `otterworks_demo` |
| DynamoDB (LocalStack) | endpoint `http://localhost:4566`, region `us-east-1`, table `otterworks-file-metadata`, namespace attribute `ns = "demo"` |

## Workloads

| Contract | Source | Atlas collection |
|---|---|---|
| `mongo-customers.md` | Oracle `CUSTOMER_MASTER` + `ENTITY_ATTR_VALUE` | `customers` |
| `mongo-invoices.md` | Oracle `INVOICE_HEADER` + `INVOICE_LINE` | `invoices` |
| `mongo-documents.md` | Postgres `documents` / `document_versions` / `document_snapshots` | `documents` |
| `mongo-files.md` | DynamoDB `otterworks-file-metadata` | `files` |
