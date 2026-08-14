# MongoDB Atlas migration tooling (tech-partnerships)

Migrates the seeded legacy estates (`make seed-legacy NS=<ns>`) into MongoDB
Atlas and reconciles the result against the seed manifest. The "after" state
for the MongoDB partner demo — see `docs/tech-partnerships/runbook-mongodb.md`.

| Source | Atlas target (`ow_tp_<ns>`) | Modeling move |
|---|---|---|
| Postgres `otterworks_<ns>.documents` + `document_versions` | `documents` | versions embedded as a bounded subarray (single-fetch reads) |
| Postgres `otterworks_<ns>.document_snapshots` | `document_snapshots` | referenced by `document_id` (unbounded blobs; orphans preserved) |
| DynamoDB `otterworks-file-metadata` (ns slice) | `files` | 1:1 item-per-document; `ns` attribute → `tenant` field |

All scripts are namespaced (`--ns`), idempotent (each run rebuilds only the
`ow_tp_<ns>` collections), and streaming (server-side Postgres cursors,
paginated DynamoDB scans, batched bulk writes — nothing materialized in
memory). Atlas-side config (access list, demo user, imported cluster) is
Terraform-managed under `infrastructure/terraform-atlas/`.

## Usage

```bash
export MONGODB_ATLAS_URI='mongodb+srv://…'   # org secret; never commit

make seed-legacy NS=dev            # source estates (Postgres/DynamoDB/S3)
make mongo-migrate NS=dev          # both migrations
make mongo-recon  NS=dev           # counts + checksums + spot samples + anomaly ledger
make mongo-clean  NS=dev           # drop ow_tp_dev from Atlas
```

Recon re-derives the manifest's order-independent md5-sum checksums from the
Atlas collections using the exact seed line formats, spot-samples documents
and files field-by-field against the live sources, and re-enumerates every
planted anomaly (version gaps, orphaned snapshots, orphaned-metadata markers)
from Atlas — anomalies must be found and accounted for, never dropped. The
report lands in `migrations/mongodb/reports/<ns>.json` (gitignored).

## Constraints

- The shared `otterworks-demo` cluster is an M0 (512 MB): demo scale only
  (`SCALE=demo`; the `full` scale does not fit).
- Only ever touch `ow_tp_<ns>` databases — the cluster is shared.
