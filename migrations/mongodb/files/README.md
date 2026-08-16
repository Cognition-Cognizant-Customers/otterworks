# `files` — DynamoDB file metadata → Atlas (tech-partnerships)

Migrates one namespace's slice of the shared DynamoDB table
`otterworks-file-metadata` (LocalStack, file-service shape) into
`ow_tp_<ns>.files` on MongoDB Atlas. Contract:
`docs/tech-partnerships/contracts/mongo-files.md`.

Additive: nothing here is on the golden `make up` / `make test` path, and the
Atlas project/cluster/user (`infrastructure/terraform-atlas/`) is owned by the
shared stack — this workload only ever creates or writes the `files` collection.

## Shape

| Stage | File | Notes |
|---|---|---|
| extract | `extract.py` | paginated `Scan` filtered server-side to `ns`, streamed in batches (never materializes the slice) |
| transform | `transform.py` | pure, unit-tested: BSON int64 fidelity, orphan-marker detection, timestamp parsing |
| load | `load.py` | `ReplaceOne(..., upsert=True)` by `_id` in bulk — idempotent per namespace |
| driver | `migrate.py` | wires the three stages, reports counts and anomalies |
| infra | `setup_collection.py` | creates the collection + its indexes (idempotent) |

Modeling decisions that the recon depends on:

- `size_bytes` is stored as BSON int64 (`bson.Int64`), so it round-trips exactly;
  a float would break the manifest checksum.
- The source `ns` attribute becomes `tenant`; no other field carries the
  namespace and the loader refuses any document whose `tenant` is not the
  namespace being migrated.
- Orphans are **flagged in place**, not quarantined: an item whose `s3_key`
  starts with `<ns>/missing/` is migrated with `storage.present: false` and
  `storage.orphanReason: "missing_object_marker"`. The key marker is the only
  signal — no S3 objects exist for any seeded item, so object existence must not
  be used.
- Timestamps become BSON dates; a value that fails to parse is kept as the raw
  string and reported, never dropped.
- Every document carries `_migration: { ns, sourceTable, migratedAt }`.

## Run

`MONGODB_ATLAS_URI` carries the Atlas credentials; the DynamoDB source uses the
usual LocalStack defaults (`AWS_ENDPOINT_URL`, `AWS_REGION`).

```bash
make infra-up                             # LocalStack (DynamoDB)
make seed-legacy NS=demo SCALE=demo       # source data + manifest
make seed-legacy-validate NS=demo         # 15/15 before-state checks

uv run migrations/mongodb/files/setup_collection.py --ns demo   # collection + indexes
uv run migrations/mongodb/files/migrate.py --ns demo            # extract → transform → load
uv run migrations/mongodb/files/migrate.py --ns demo            # idempotent: same numbers
```

## Tests

```bash
uv run --with pytest==8.3.5 --with pymongo==4.10.1 pytest migrations/mongodb/files/tests -q
```

Unit tests only — no Atlas, no LocalStack: the transformer is pure and the
extractor/loader are exercised against table/collection stubs.
