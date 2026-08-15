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
