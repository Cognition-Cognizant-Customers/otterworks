# Contract — `mongo-files`: DynamoDB file metadata → Atlas `files`

Read `mongo-README.md` in this directory first: it holds the shared rules, checksum
definitions and source connection details that this contract depends on.

## Source — DynamoDB table `otterworks-file-metadata` (LocalStack)

Shared table; this namespace's slice is the items with `ns = "demo"`. Item shape
(file-service shape, attribute names verbatim):

`id` (plain UUID, partition key), `ns`, `name`, `mime_type`, `size_bytes`
(number), `s3_key`, `folder_id`, `owner_id`, `version` (number),
`is_trashed` (bool), `created_at`, `updated_at` (ISO-8601 strings).

The scan paginates — sum pages, never assume a single page. Ownership follows a
power law. `s3_key` is `"<ns>/<prefix>/<owner_id>/<file_uuid>"` where `prefix` is
`files` for healthy items and `missing` for the planted orphans.

## Target — `ow_tp_demo.files`

1:1 item-per-document — the source is already a document store, so the move is a
type-fidelity and tenancy exercise, not a remodel:

```js
{
  _id: "<id>",                            // deterministic: source partition key
  tenant: "demo",                         // ns attribute → tenant field
  name: "file-demo-0000001.pdf",
  mimeType: "application/pdf",
  sizeBytes: NumberLong(12345678),        // BSON int64, not a double
  storage: { s3Key: "demo/files/<owner>/<uuid>", present: true },
  folderId: "<folder_id>", ownerId: "<owner_id>",
  version: 3,
  isTrashed: false,
  createdAt: ISODate, updatedAt: ISODate,
  _migration: { ns: "demo", sourceTable: "dynamodb:otterworks-file-metadata",
                migratedAt: ISODate }
}
```

Modeling rules:
- `size_bytes` must round-trip exactly: store as BSON int64 (`NumberLong`), and
  the recon must read it back as an integer (a float round-trip breaks the
  checksum).
- Timestamp strings become BSON dates; keep the raw string only if a value fails
  to parse (none are planted, but the code must not silently drop one).
- The namespace attribute becomes the `tenant` field — no other field carries the
  namespace, and the loader must never write items from another namespace.
- Orphan handling is **flag-in-place, not quarantine**: an item whose `s3_key`
  carries the `demo/missing/…` marker is migrated normally with
  `storage.present: false` and `storage.orphanReason: "missing_object_marker"`.
  The anomaly signal is the key pattern only — no S3 objects are written for any
  seeded item, so object existence must **not** be used as the signal.
- Indexes (PR 1): `{ tenant: 1, ownerId: 1 }`, `{ folderId: 1 }`,
  `{ isTrashed: 1 }`, `{ "storage.s3Key": 1 }` (unique).

## Expected results (must match exactly)

| Metric | Expected |
|---|---|
| `files` documents (tenant `demo`) | **10,000** |
| Checksum over `files` | **`db614663b2c6d41141cae82261b416d5`** |
| Documents with `storage.present: false` | **40** |

Checksum recomputation from Atlas (order-independent sum of md5, see README):
one line per document, `f"{_id}|{sizeBytes}|{storage.s3Key}"`, with `sizeBytes`
rendered as a plain integer (no decimal point, no exponent).

## Planted anomalies this workload must detect and report

| Kind | Manifest target | Count |
|---|---|---|
| `orphaned_metadata` | `dynamodb.file-metadata` | **40** |

Report exactly **40** orphaned-metadata items, with their ids and `s3_key`s, and
show they are the documents carrying `storage.present: false`.

## Deliverable — 3-PR stack into the working branch

1. Workload infra: indexes / collection setup for `files` (never touch
   `infrastructure/terraform-atlas/`).
2. `migrations/mongodb/files/` — extractor (paginated DynamoDB scan filtered to
   `ns = "demo"`, boto3 against the LocalStack endpoint), transformer (pure,
   unit-tested: int64 fidelity, orphan-marker detection, timestamp parsing),
   loader (idempotent upsert by `_id`, bulk writes).
3. Recon: script plus committed output — count, checksum comparison against the
   manifest, and the anomaly ledger.
