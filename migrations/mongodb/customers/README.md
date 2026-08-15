# `mongo-customers` — Oracle `CUSTOMER_MASTER` + EAV → Atlas `customers`

Migration workload for the tech-partnerships MongoDB track. Contract:
`docs/tech-partnerships/contracts/mongo-customers.md`. Additive to the golden
app: nothing here is on the `make up` / `make test` path.

## Collections owned by this workload

| Collection | Contents |
|---|---|
| `customers` | one document per `CUSTOMER_MASTER` row in the namespace's conversion batch |
| `customers_quarantine` | one document per quarantined *field* (dirty date, malformed CSV list); the customer itself is still migrated |

Target database is `$MONGO_DB` (default `ow_tp_demo`). Indexes live in
`config.INDEXES` and are applied by `setup_collections.py`.

## Usage

```bash
# 0. before-state (see docs/tech-partnerships/runbook-mongodb.md)
make oracle-billing-up && make oracle-billing-seed NS=demo SCALE=demo

# 1. collections + indexes (idempotent)
make mongo-tp-customers-setup

# 2. migrate (idempotent; rerunning yields identical recon numbers)
make mongo-tp-customers-migrate NS=demo

# 3. recon against testdata/legacy/manifests/<ns>.json, recomputed from Atlas
make mongo-tp-customers-recon NS=demo

# unit tests (pure transformer; no Oracle/Atlas needed)
make mongo-tp-customers-test
```

`MONGODB_ATLAS_URI` must be exported (never committed, never printed), and the
VM's public IP must be in the Atlas project access list.

## Pipeline

| Module | Role |
|---|---|
| `extract.py` | batched Oracle reads: `fetchmany` over the batch's `CUSTOMER_MASTER` rows in `_id` order, one bound `IN (...)` EAV lookup per chunk |
| `transform.py` | pure row → document mapping, no I/O; unit-tested in `test_transform.py` |
| `load.py` | unordered `ReplaceOne(upsert=True)` batches keyed on deterministic `_id`s |
| `migrate.py` | driver + per-run counters (`LIMIT=n`, `DRY_RUN=1`) |
| `recon.py` | recomputes counts/checksum/anomaly ledger from Atlas and compares them to the manifest; writes `docs/tech-partnerships/recon/mongo-customers.md` |

## Modeling notes

- Sparse columns are omitted, never stored as `null`; empty repeating-group
  slots produce no array entry.
- Anomalies are preserved, never repaired: an unparseable value is kept raw
  under `_quarantine.<COLUMN>` (parsed field omitted) and gets a
  `customers_quarantine` ledger document. The customer is still migrated, so
  document counts and the checksum stay equal to the source.
- `attributes` is keyed by `ATTR_NAME`, so EAV rows sharing a name for one
  customer compete for a single slot. The winner is deterministic — greatest
  `CREATED_DT`, ties broken by the lexicographically greatest `ATTR_VALUE` —
  and the losing rows are preserved under `legacy.attributeConflicts`, so
  folded keys + conflict entries account for every source row.
- Idempotency: every `_id` is derived from the source (`CUST_ID`, or
  `"<CUST_ID>:<COLUMN>"` for a ledger entry), so a second run upserts over the
  first and recon reports the same numbers. Only `_migration.migratedAt`
  changes between runs. Verified on NS=demo: the second run reports
  `customers_upserted: 0` / `customers_matched: 25000` and its recon report is
  identical to the first apart from the generation timestamp.
