# MongoDB migrations (tech-partnerships track)

One migration per workload unit, per the contracts under
`docs/tech-partnerships/contracts/`. Everything is deterministic and
namespaced: target databases are `ow_tp_mongodb_<ns>` (quarantine in
`ow_tp_mongodb_<ns>_quarantine`), document ids are uuid5 of stable source
keys, and reruns are idempotent upserts.

## mongo_customers

Oracle `OW_BILLING.CUSTOMER_MASTER` (155 cols) + `ENTITY_ATTR_VALUE` →
`customers`, with malformed CSV lists and dirty `SIGNUP_DT` values quarantined
into `customers_quarantine` (contract:
`docs/tech-partnerships/contracts/mongo_customers.json`).

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
