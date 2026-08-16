# `mongo-documents` — Postgres versioned documents → Atlas

Migration workload for the tech-partnerships MongoDB track. Source is the legacy
document-service estate in Postgres schema `otterworks_<ns>` (`documents`,
`document_versions`, `document_snapshots`, seeded by `make seed-legacy`); target
is the Atlas database `ow_tp_demo`.

## Target model

| Collection | `_id` | Shape |
|---|---|---|
| `documents` | source `documents.id` | document + **embedded** `versions[]` (fan-out is 2–12; read pattern is "open a document and its history"), `declaredVersion` copied verbatim from `documents.version`, `versionGap` when versions are missing, `snapshotIds` references |
| `document_snapshots` | source `document_snapshots.id` | opaque CRDT blob kept out of the document (different size/lifecycle), `documentId` reference |
| `document_snapshots_orphaned` | source `document_snapshots.id` | snapshots whose `document_id` does not exist, `quarantine_reason: "missing_document"` — quarantined, never dropped |

Indexes: `documents {ownerId: 1, updatedAt: -1}`, `{folderId: 1}`, `{isDeleted: 1}`;
both snapshot collections `{documentId: 1}`; and `{_migration.ns: 1}` on all
three, since the collections are shared across namespaces and every recon read
is scoped by that field.

Every document carries `_migration: { ns, sourceTable, migratedAt }`.

## Rules this workload follows

- `declaredVersion` is never recomputed: the planted version gaps are exactly the
  documents where `declaredVersion != versionCount`, and the recon depends on that
  difference surviving the migration.
- Anomalies (version gaps, orphaned snapshots) are preserved and reported, never
  silently repaired.
- The loader is idempotent per namespace: `_id` is the deterministic source PK, so
  a rerun upserts in place and recon numbers are identical.
- Only the three collections above are created/written. The shared cluster,
  `infrastructure/terraform-atlas/`, and other namespaces are never touched.

## Commands

`MONGODB_ATLAS_URI` must be set in the environment (never passed on the command
line, never written to a file).

```bash
# 1. workload infra (idempotent; --drop for a clean slate)
uv run migrations/mongodb/documents/setup_collections.py

# 2. migrate (idempotent: safe to run repeatedly, same recon numbers)
uv run migrations/mongodb/documents/migrate.py --ns demo

# 3. recon: recompute counts/checksums/anomalies FROM ATLAS against the manifest,
#    and prove a rerun changed nothing by diffing against an earlier recon
uv run migrations/mongodb/documents/recon.py --ns demo --json /tmp/recon_run1.json
uv run migrations/mongodb/documents/migrate.py --ns demo
uv run migrations/mongodb/documents/recon.py --ns demo \
  --compare-json /tmp/recon_run1.json \
  --report docs/tech-partnerships/recon/mongo-documents-demo.md \
  --json docs/tech-partnerships/recon/mongo-documents-demo.json

# transformer unit tests (pure, no database needed)
uv run --no-project --with pytest python -m pytest migrations/mongodb/documents/tests
```

The committed evidence lives in `docs/tech-partnerships/recon/`; `recon.py` exits
non-zero when any check fails.

## Layout

| File | Role |
|---|---|
| `mongo_common.py` | Atlas connection, owned collection names, bridge to `testdata/legacy/legacy_common.py` |
| `extract.py` | server-side cursor over `documents`, batched with each batch's versions/snapshots; separate stream for orphaned snapshots |
| `transform.py` | pure row → document mapping (gap detection, snapshot routing, NULL `folder_id`) |
| `load.py` | idempotent `ReplaceOne` upserts by `_id` |
| `migrate.py` | entrypoint wiring extract → transform → load |
| `recon.py` | Atlas-side recon: streams the migrated collections, recomputes counts/checksums/anomaly ledger, compares against the manifest and an earlier recon |
| `tests/` | transformer unit tests, including the planted anomaly cases |
