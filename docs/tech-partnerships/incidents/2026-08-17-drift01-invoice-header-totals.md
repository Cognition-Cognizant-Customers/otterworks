# Incident: drift01 invoice header totals diverged from embedded lines

- **Date:** 2026-08-17 (UTC)
- **Namespace / database:** `drift01` / `ow_tp_drift01` (MongoDB Atlas, cluster `otterworks-demo`)
- **Detected by:** `make tp-mongo-recon-platform NS=drift01 RUN_MODE=live` (`scripts/tp_mongo/recon_platform.py`), which exited RED and POSTed the failure webhook to the Devin recon automation.
- **Status:** resolved — full live reconciliation GREEN (87 checks), idempotency reruns pass, planted-anomaly sets match exactly.

## What reconciliation caught

One check failed:

| check id | expected | actual |
|---|---|---|
| `platform.invoice_header_total_matches_lines` | 0 | **18** |

Source of truth (from the webhook payload): *"ow_tp_drift01.invoices: invoices whose
migrated header total_amt does not equal the Decimal128 sum of its own embedded line
amounts, summed by the server before any migration was re-run"*.

`planted_anomalies_missing` and `planted_anomalies_unexpected` were both empty — the
quarantine collections were intact; only invoice header money had drifted.

## Root cause

Post-migration data drift in the target, not a migration-code defect: money values on a
slice of 18 migrated invoice documents were mutated in place after migration, leaving
each header `total_amt` out of agreement with the Decimal128 sum of its own embedded
lines. The shape of the failure isolates the cause:

- `invoices` count (18,750), embedded line total (149,963 = 150,000 source rows minus 37
  quarantined orphans) and `platform.invoice_line_count_matches_embedded` were all
  clean — no lines were lost, so this was value drift on money fields
  (the `round_invoice_money` class of drift: still valid Decimal128, so the
  `$jsonSchema` validator has nothing to object to; only reconciliation can catch it).
- The baseline manifest (`testdata/legacy/manifests/drift01.json`, seed `2666753027`)
  and the legacy Oracle estate were untouched.

## Remediation

No repository code needed changing. The repair is the idempotent migration itself:

1. The platform recon measures drift **before** any rerun, then each unit recon re-runs
   its own migration as its idempotency proof (`recon_invoices.py` always re-runs
   `migrate_invoices.py`). The rerun re-derived the 18 damaged headers from the immutable
   Oracle source and upserted them back to correct Decimal128 values.
2. This session re-ran the full live reconciliation to prove the estate green
   end-to-end. The first pass surfaced a transient `customers.idempotent` failure
   (canonical-JSON fingerprints `afb1cf7e…/3e349bf4…` → `d8c29de1…/fa33d142…`): the
   customers rerun converged residual representational differences from the original
   migration run onto the current branch's canonical output. Every baseline-anchored
   customers check (count 25,000; balance checksum `aba7ca871d775c70a3de243835d72a2c`;
   folded EAV 8,333; planted anomalies `dirty_dates:50`, `malformed_csv_lists:31`)
   passed both before and after that rerun — no baseline value changed.
3. The second full pass is GREEN with the rerun a proven no-op.

No target document was hand-edited; no baseline manifest, legacy estate object, or
recon check was modified.

## Evidence

Before (webhook payload from the RED run):

```json
{
  "id": "platform.invoice_header_total_matches_lines",
  "actual": 18,
  "expected": 0
}
```

After (this session, `MONGO_URI=<target-uri> make tp-mongo-recon-platform NS=drift01 RUN_MODE=live`):

```text
[recon-platform] target volumes before any migration rerun: {... 'invoices': 18750,
  'invoices.embedded_lines': 149963, 'invoices.header_total_mismatches': 0}
[recon-platform] customers recon exit=0 checks=18
[recon-platform] invoices  recon exit=0 checks=16
[recon-platform] documents recon exit=0 checks=20
[recon-platform] files     recon exit=0 checks=12
[recon-platform] GREEN: 87 checks passed for ns=drift01
```

- Full green report (schema-validated with `make tp-validate-recon`):
  [`2026-08-17-drift01-mongo-platform.recon.green.json`](./2026-08-17-drift01-mongo-platform.recon.green.json)
- Direct collection read-back after repair: the 18 lowest-`_id` invoices in
  `ow_tp_drift01.invoices` all satisfy `total_amt == Σ lines.amount` to the cent, with
  fractional precision restored (e.g. `_id 000f0565-8e6c-…`: header `16441.04` == sum of
  5 embedded lines `16441.04`), and the server-side aggregation over all 18,750 invoices
  reports `invoices.header_total_mismatches: 0`.
- Invoice baseline checksums match the immutable manifest:
  `invoices.checksum a9417dd8c0ca1226122a62f4c28a38fc`,
  `invoices.checksum_non_orphan_lines ea9ac12556858c977905476c9e1a7114`.
- Planted-anomaly sets exact in every unit: `orphaned_rows` (invoices, 37),
  `dirty_dates:50` + `malformed_csv_lists:31` (customers), missing/unexpected both empty.

## Operational notes

- Atlas access for this remediation used a temporary session-scoped IP access-list
  entry and a temporary database user scoped to `ow_tp_drift01` only
  (`readWrite` + `dbAdmin` on that database, nothing shared); both were removed after
  the green run. No other namespace's objects were touched.
- The failure webhook fires only on RED; the green run correctly did not notify.
