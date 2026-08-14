# Legacy Seed-Data Generators (tech-partnerships)

Deterministic, per-namespace seed generators that give the partner migration
demos real data volume in the app's core stores. Part of the tech-partnerships
demo track — see `docs/tech-partnerships/README.md` for the build contracts,
including the seed-manifest JSON contract these generators write to.

Everything here is additive: nothing on the golden `make up` / `make test`
path invokes these targets.

## Targets seeded

| Target | Store | What |
|---|---|---|
| `postgres` | `otterworks_<ns>` schema on the shared Postgres | `documents`, `document_versions`, `document_snapshots` (document-service shapes; snapshots mirror the collab-service archiver) |
| `dynamodb` | `otterworks-file-metadata` table (LocalStack) | file-metadata items (file-service shape), namespaced by an `<ns>#` id prefix |
| `s3` | `s3://otterworks-data-lake/events/<ns>/` (LocalStack) | hourly gzip JSON event objects, one per hour |

The Oracle billing estate is seeded by its own generators under
`services/legacy-billing`; this framework only merges with (never clobbers) any
entries it finds in the shared manifest.

## Usage

```bash
make infra-up                        # Postgres + LocalStack must be running

# Seed (SCALE defaults to demo; TARGETS defaults to all three)
make seed-legacy NS=dev
make seed-legacy NS=dev SCALE=full
make seed-legacy NS=dev TARGETS=postgres,s3

# Validate: re-derives counts/checksums from the stores and asserts they
# match the manifest (including planted-anomaly enumeration)
make seed-legacy-validate NS=dev

# Clean up the Postgres slice (DynamoDB/S3 slices are wiped on each rerun)
make testdata-clean NS=dev
```

## Volumes

| Scale | Documents | Versions/doc | DynamoDB items | Event history |
|---|---|---|---|---|
| `demo` | 2,000 | 2–12 | 10,000 | 3 days hourly (~72 objects) |
| `full` | 100,000 | 10–50 | 500,000 | 90 days hourly (~2,160 objects) |

Ownership follows a power-law: a few whale users own most documents and files.
`demo` seeds in well under 15 minutes against `make infra-up` infra.

## Determinism

- The RNG seed is derived from the namespace
  (`int(sha256(ns)[:8], 16)`), so a namespace reproduces byte-identical
  counts, checksums, and manifests across reruns.
- All timestamps derive from a fixed anchor (`2026-08-01T00:00:00Z`), never
  wall-clock time; the manifest's `generated_at` is that anchor, so rerunning
  a seed produces an identical manifest file.
- Gzip objects are written with `mtime=0` so object bytes are reproducible.
- Reruns first wipe the namespace's slice of each store (truncate the
  Postgres tables, delete `<ns>#`-prefixed DynamoDB items, delete the
  `events/<ns>/` S3 prefix) and reseed from scratch.

## Planted anomalies

Deliberate, exactly-enumerable data-quality defects for migration
reconciliation to find, recorded in the manifest's `planted_anomalies`:

| Kind | Target | Defect |
|---|---|---|
| `version_gaps` | `postgres.…document_versions` | documents whose declared `version` count has a missing version row |
| `orphaned_snapshots` | `postgres.…document_snapshots` | snapshots whose `document_id` doesn't exist |
| `orphaned_metadata` | `dynamodb.file-metadata` | items whose `s3_key` carries the `<ns>/missing/…` marker; the anomaly is key-pattern-only — no objects are written to `otterworks-files` for any seeded item, so reconciliation should treat the marker (not object existence) as the defect signal |
| `missing_hours` | `s3.data-lake/events/<ns>/` | gaps in the hourly event-object series |

The validator re-enumerates each anomaly from the store and asserts the count
matches the manifest exactly.

## Manifest

Written/merged to `testdata/legacy/manifests/<ns>.json` per the contract in
`docs/tech-partnerships/README.md`. Merge semantics: only the target keys this
run seeded are replaced; entries owned by other estates (e.g. `oracle.*`) are
preserved. Manifests are runtime artifacts and are gitignored.

Each seeded target records its run parameters under
`seed_legacy_params.<target>` (including `scale`), so a partial re-seed via
`TARGETS=` never rewrites the recorded parameters of stores it didn't touch.

Checksum definition: order-independent sum of per-line md5 digests
(mod 2^128), rendered as 32 hex chars. Line formats:

| Target | Line format |
|---|---|
| `documents` | `id\|version\|word_count` |
| `document_versions` | `document_id\|version_number` |
| `document_snapshots` | `id\|document_id` |
| `dynamodb.file-metadata` | `id\|size_bytes\|s3_key` |
| `s3.data-lake/events/<ns>/` | `key\|event_count\|gzip_bytes` |

## Connection defaults

Same as the rest of `testdata/`: `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/
`DB_PASSWORD` (default local Docker Postgres), plus `AWS_ENDPOINT_URL`
(default `http://localhost:4566`, LocalStack) and `AWS_REGION`
(default `us-east-1`).
