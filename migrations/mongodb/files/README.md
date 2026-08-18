# mongo_files — DynamoDB file metadata → MongoDB `files`

Unit contract: `docs/tech-partnerships/contracts/mongo_files.json`.

Migrates `otterworks-file-metadata` items where `ns=<ns>` (LocalStack in the
fixture phase) 1:1 item-per-document into `ow_tp_mongodb_<ns>.files` (`ns` →
`tenant`), quarantining `orphaned_metadata` items with attribution into
`ow_tp_mongodb_<ns>_quarantine.files_quarantine`. The target is addressed only
via `--mongo-uri` / `MONGODB_URI` so the parent can point the identical code at
Atlas in the live validation window.

```bash
# migrate (idempotent; per-batch upserts; empty input is a no-op)
scripts/tp-run-deterministic.sh uv run migrations/mongodb/files/migrate.py --ns demo

# recon: snapshot after run 1, rerun, then emit the schema-valid report
scripts/tp-run-deterministic.sh uv run migrations/mongodb/files/recon.py \
    --ns demo --mode snapshot --out /tmp/mongo_files-demo.snap.json
scripts/tp-run-deterministic.sh uv run migrations/mongodb/files/migrate.py --ns demo
scripts/tp-run-deterministic.sh uv run migrations/mongodb/files/recon.py \
    --ns demo --mode report --run-mode fixture \
    --prior /tmp/mongo_files-demo.snap.json \
    --out docs/tech-partnerships/recon/mongo_files-demo.recon.json

make tp-validate-recon FILE=docs/tech-partnerships/recon/mongo_files-demo.recon.json
```

The recon report recomputes every value from the target collections (count,
order-independent md5 over the seeder's `id|size_bytes|s3_key` line format,
quarantine enumeration) and compares planted anomalies as sets; the
`missing_hours` anomaly is a declared contractual coverage gap carried through
the report's attribution.
