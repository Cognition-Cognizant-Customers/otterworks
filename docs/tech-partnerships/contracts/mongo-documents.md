# Contract — `mongo-documents`: Postgres versioned documents → Atlas `documents`

Read `README.md` in this directory first: it holds the shared rules, checksum
definitions and source connection details that this contract depends on.

## Source — Postgres schema `otterworks_demo`

| Table | Columns |
|---|---|
| `documents` | `id`, `title`, `content`, `content_type`, `owner_id`, `folder_id` (nullable), `is_deleted`, `is_template`, `word_count`, `version` (declared latest version), `created_at`, `updated_at` |
| `document_versions` | `id`, `document_id`, `version_number`, `title`, `content`, `created_by`, `created_at` |
| `document_snapshots` | `id`, `document_id`, `state_b64`, `label`, `created_by`, `created_at` (collab-service archiver shape) |

Ownership follows a power law (a few whale owners hold most documents), 2–12
versions per document, ~20% of documents carry a snapshot.

## Target — `ow_tp_demo.documents` (+ `document_snapshots`, `document_snapshots_orphaned`)

Versions are **embedded** as a bounded subarray — the fan-out is 2–12 and the
read pattern is "open a document and its history":

```js
{
  _id: "<documents.id>",                  // deterministic: source PK
  title, contentType: "text/markdown",
  content: "<latest body>",
  ownerId: "<owner_id>", folderId: "<folder_id>",   // folderId omitted when NULL
  isDeleted: false, isTemplate: false,
  wordCount: 42,
  declaredVersion: 7,                     // documents.version, verbatim from source
  versionCount: 6,                        // versions actually present
  versions: [
    { versionId, versionNumber, title, content, createdBy, createdAt: ISODate }
  ],                                      // ascending versionNumber
  versionGap: { missing: [4], expected: 7, present: 6 },  // only when a gap exists
  snapshotIds: ["<snapshot id>"],         // references, snapshots are not embedded
  createdAt: ISODate, updatedAt: ISODate,
  _migration: { ns: "demo", sourceTable: "otterworks_demo.documents", migratedAt: ISODate }
}
```

Modeling rules:
- `declaredVersion` is copied from the source `version` column and is **never**
  recomputed — the planted version gaps are exactly the documents where
  `declaredVersion != versionCount`, and the recon depends on that difference
  surviving the migration.
- Snapshots are a separate collection (`document_snapshots`, `_id` = source id,
  `documentId` reference) because they are large opaque CRDT blobs with a
  different lifecycle; each migrated document carries `snapshotIds`.
- Snapshots whose `document_id` does not exist in `documents` are **orphans**:
  they go to `document_snapshots_orphaned` with
  `quarantine_reason: "missing_document"`, never dropped.
- Timestamps become BSON dates (UTC).
- Indexes (PR 1): `{ ownerId: 1, updatedAt: -1 }`, `{ folderId: 1 }`,
  `{ isDeleted: 1 }`, and on snapshots `{ documentId: 1 }`.

## Expected results (must match exactly)

| Metric | Expected |
|---|---|
| `documents` documents | **2,000** |
| Embedded versions across all documents | **13,876** |
| Snapshot documents (`document_snapshots` + orphaned) | **390** |
| `document_snapshots_orphaned` documents | **6** |
| `documents` checksum | **`e70001cf6110014dab6e1d80adb40285`** |
| `document_versions` checksum | **`13bc033b2780a0569d7f2217e85d7303`** |
| `document_snapshots` checksum | **`abe69084205723d6ad79e825b8c752dd`** |

Checksum recomputation from Atlas (order-independent sum of md5, see README):

| Manifest target | Line, recomputed from Atlas |
|---|---|
| `documents` | `f"{_id}\|{declaredVersion}\|{wordCount}"` — one line per document |
| `document_versions` | `f"{_id}\|{v.versionNumber}"` — one line per embedded version |
| `document_snapshots` | `f"{snap._id}\|{snap.documentId}"` — over `document_snapshots` **and** `document_snapshots_orphaned` (the 6 orphans are part of the source set) |

## Planted anomalies this workload must detect and report

| Kind | Manifest target | Count |
|---|---|---|
| `version_gaps` | `postgres.otterworks_demo.document_versions` | **10** |
| `orphaned_snapshots` | `postgres.otterworks_demo.document_snapshots` | **6** |

- A version gap is a document whose declared `version` count has a missing
  version row in the middle of the series. Report exactly **10**, with the
  document ids and the missing version numbers (from `versionGap.missing`).
- Report exactly **6** orphaned snapshots, with snapshot ids and their dangling
  `document_id`s, and show that all 6 landed in
  `document_snapshots_orphaned`.

## Deliverable — 3-PR stack into the working branch

1. Workload infra: indexes / collection setup for `documents`,
   `document_snapshots`, `document_snapshots_orphaned` (never touch
   `infrastructure/terraform-atlas/`).
2. `migrations/mongodb/documents/` — extractor (Postgres, server-side cursor,
   batched by document), transformer (pure, unit-tested: gap detection,
   snapshot routing, NULL `folder_id`), loader (idempotent upsert by `_id`).
3. Recon: script plus committed output — counts, the three checksum
   comparisons against the manifest, and the anomaly ledger.
