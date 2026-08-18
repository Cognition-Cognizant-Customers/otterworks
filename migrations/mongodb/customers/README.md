# mongo_customers migration unit

Migrates `OW_BILLING.CUSTOMER_MASTER` (155 columns) + `ENTITY_ATTR_VALUE`
into one document per customer, per
`docs/tech-partnerships/contracts/mongo_customers.json`.

- Target: `ow_tp_mongodb_<ns>.customers`; quarantine:
  `ow_tp_mongodb_<ns>_quarantine.customers_quarantine`.
- EAV rows fold into an `attributes` subdocument (`{NAME: [entries...]}`,
  preserving duplicate attribute rows); `RELATED_ACCT_IDS` /
  `PROMO_CODES_CSV` become real arrays; valid `DD-MON-YY` strings become
  BSON dates. Malformed CSV lists and dirty dates are quarantined with
  attribution (source PK, field, raw value, reason); NULL source values are
  omitted fields, never fabricated defaults.

## Run (fixture)

```bash
# local MongoDB fixture on mongodb://localhost:27017 (override with MONGODB_URI)
scripts/tp-run-deterministic.sh uv run migrations/mongodb/customers/migrate.py --ns demo
scripts/tp-run-deterministic.sh uv run migrations/mongodb/customers/recon.py --ns demo \
  --idempotency-evidence "<evidence from an actual rerun>"
```

The recon report recomputes all counts/checksums from the target MongoDB and
lands at `docs/tech-partnerships/recon/mongo_customers-<ns>.recon.json`
(schema-gated by `make tp-validate-recon`). For the parent's live window,
point `MONGODB_URI` at Atlas and pass `--run-mode live`.
