# migrations/mongodb — MongoDB track migration units

One script pair per workload unit (see `docs/tech-partnerships/contracts/`).
Each unit migrates its legacy slice into the run-scoped databases
`ow_tp_mongodb_<ns>` / `ow_tp_mongodb_<ns>_quarantine` and ships a recon
generator that recomputes counts, checksums, and anomaly sets **from the
target MongoDB**, never from migration-time memory.

Shared rules:

- Deterministic and namespaced: every script takes `--ns`; document ids are
  the deterministic source ids (or `uuid5` derivations for quarantine
  records) — never `uuid4`, never wall-clock values inside documents.
- Idempotent: reruns upsert by `_id` and reproduce identical recon numbers.
  Nothing here ever drops or truncates a collection.
- Empty input is a no-op: a run with no items for the namespace writes
  nothing and leaves prior target output untouched.
- Local fixture first: point `--mongodb-uri` at a local deployment
  (`atlas deployments setup <name> --type local`); recon reports from it are
  `run_mode: fixture`. The live window is parent-owned.

## mongo_files (DynamoDB `otterworks-file-metadata` → `files`)

```bash
uv run migrations/mongodb/migrate_files.py --ns demo \
    --mongodb-uri "mongodb://localhost:27778/?directConnection=true"

uv run migrations/mongodb/recon_files.py --ns demo \
    --mongodb-uri "mongodb://localhost:27778/?directConnection=true" \
    --out docs/tech-partnerships/recon/mongo_files.demo.recon.json

make tp-validate-recon FILE=docs/tech-partnerships/recon/mongo_files.demo.recon.json
```

Idempotency proof: run migrate → recon (baseline), migrate again → recon with
`--compare <baseline>`; the second report embeds the rerun evidence and fails
if any recomputed value moved.
