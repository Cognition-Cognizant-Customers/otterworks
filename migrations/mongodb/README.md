# MongoDB migration units (tech-partnerships track)

One directory per workload unit, each owned by exactly one child session and
delivered as exactly one PR into the run's `tp-run/mongodb-*` branch. Contracts
live in `docs/tech-partnerships/contracts/mongo_*.json`; recon reports go to
`docs/tech-partnerships/recon/*.recon.json` and must validate with
`make tp-validate-recon FILE=<report>`.

Shared rules:

- Everything is namespaced: target databases are `ow_tp_mongodb_<ns>` and
  `ow_tp_mongodb_<ns>_quarantine`. Never touch another unit's collections and
  never run DDL/index changes on a collection another workload reads or writes.
- Migrations are deterministic and idempotent: upserts keyed on uuid5-derived
  `_id`s (never uuid4), no wall-clock timestamps embedded in documents, so a
  rerun for the same namespace reproduces identical recon numbers.
- Development and self-verification run against a local MongoDB fixture
  (`docker run mongo` or `atlas deployments setup --type local`); fixture recon
  reports carry `run_mode: "fixture"`. Only the parent's live window produces
  `run_mode: "live"` evidence against Atlas.
- Credentials are referenced by NAME only (`MONGODB_ATLAS_URI` et al.);
  the default `MONGODB_URI` points at the local fixture.

## Units

- `mongo_documents/` — Postgres `otterworks_<ns>` (`documents`,
  `document_versions`, `document_snapshots`) → `ow_tp_mongodb_<ns>.documents`
  with versions embedded as a bounded subarray and snapshots as references;
  orphaned snapshots quarantine into
  `ow_tp_mongodb_<ns>_quarantine.documents_quarantine`.
- `mongo_customers` (`migrate_customers.py` / `recon_customers.py`) — Oracle
  `OW_BILLING.CUSTOMER_MASTER` (155 cols) + `ENTITY_ATTR_VALUE` →
  `ow_tp_mongodb_<ns>.customers`, with malformed CSV lists and dirty
  `SIGNUP_DT` values quarantined into
  `ow_tp_mongodb_<ns>_quarantine.customers_quarantine` (contract:
  `docs/tech-partnerships/contracts/mongo_customers.json`).

## mongo_customers usage

```bash
# Legacy source (deterministic seed):
make oracle-billing-up && make oracle-billing-seed NS=demo

# Local MongoDB fixture (any local mongod works), e.g.:
#   atlas deployments setup <name> --type local --port 27717 --force
export MONGODB_URI=mongodb://localhost:27717   # live runs: MONGODB_ATLAS_URI

# Migrate + prove idempotency by rerun + emit the recon report:
cd migrations/mongodb
uv run --no-project --with oracledb==3.1.1 --with pymongo==4.11.3 \
  python3 verify_customers_fixture.py --ns demo --run-mode fixture \
  --out ../../docs/tech-partnerships/recon/mongo_customers.demo.fixture.recon.json

# Validate the report against the recon schema:
make -C ../.. tp-validate-recon FILE=docs/tech-partnerships/recon/mongo_customers.demo.fixture.recon.json
```

`migrate_customers.py` and `recon_customers.py` are also runnable standalone;
the recon generator recomputes every value from the target MongoDB, never
from migration-time memory.
