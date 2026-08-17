# Demo Runbook — Databricks + Devin: billing history, and what happens when recon fails

**Duration:** ~12 minutes staged, ~4 minutes for the failure beat alone.
**Story:** OtterWorks' 1998-vintage CUSTBILL chain didn't just run nightly — it
piled up years of monthly billing drops nobody can query. This demo moves that
history onto the lakehouse, proves it matches the legacy estate to the cent,
then breaks reconciliation on purpose so Databricks catches it, Devin fixes it,
and the PR proves it.

This runbook is the platform half of the Databricks track. The conversion
fan-out (one Devin child session per legacy script) is
[`runbook-databricks.md`](runbook-databricks.md); this one assumes those jobs
exist and focuses on history, platform capabilities, and the failure loop.

Everything is namespace-scoped (`NS=demo` below). All numbers are deterministic
for a given namespace, so on-screen output matches this document exactly.

## Playbooks

| Macro | Playbook id | Role |
|---|---|---|
| `!tp_dbx_demo_stage` | `playbook-2943e3f2b522444f85c2f61a09743701` | Parent: stages the whole demo, fans out children, leaves it rehearsal-ready |
| `!tp_dbx_backfill` | `playbook-377124a498024799a93ed571e5612cc0` | Child: multi-year history load + per-year recon + Delta time travel |
| `!tp_dbx_platform` | `playbook-eea8d3bfe3f14691a7895c45d2743d99` | Child: declarative pipeline, lineage, dashboard, alert, recon job + failure-to-Devin loop |
| `!tp_migrate_databricks` | `playbook-292f26f986d743d2832c427bd9992e84` | Existing: legacy-script conversion fan-out (unchanged) |

## Staged state (`NS=demo`)

| Layer | Object | Content |
|---|---|---|
| Landing | `/Volumes/ow_tp/bronze/landing/demo/history` | 72 monthly CUSTBILL drops, 2019–2024, original file names and periods (the volume stamps upload time, so drop dates come from `source_period`, not file mtimes) |
| Bronze | `ow_tp.bronze.custbill_history_raw_demo` | 3,024 raw lines incl. headers/trailers, with source file/period/year |
| Silver | `ow_tp.silver.custbill_history_demo` | 2,856 valid billing records |
| Silver | `ow_tp.silver.custbill_quarantine_demo` | 30 planted anomalies (bad dates, non-numeric amounts, trailer mismatch) |
| Gold | `ow_tp.gold.custbill_annual_demo` | 36 annual finance rows, 1,439,098,122 cents total |
| Ops | `ow_tp.ops.history_expectations_demo` | 36 legacy-derived expectation rows (the recon source of truth) |
| Ops | `ow_tp.ops.recon_runs_demo` | recon history, one row per run |
| Pipeline | `ow_tp_custbill_history_dlt_demo` | same shape, quarantine rules as declared expectations |
| Job | `ow_tp_billing_history_recon_demo` | recon SQL + `AT_LEAST_ONE_FAILED` Devin notifier, **schedule PAUSED** |

Row counts are namespace-independent; the cent totals are not — the generator
seeds amounts per namespace, so a rehearsal namespace legitimately reports a
different total (e.g. `rehearse1` = 1,440,462,121) with all recon checks green.

## Pre-demo setup

```bash
export DATABRICKS_DEMO_HOST=... DATABRICKS_DEMO_TOKEN=...   # PAT: sql, uc, jobs, secrets, workspace, files
make legacy-etl-gen-history NS=demo                # 72 dated drops, 2,880 records, 30 anomalies
make dbx-showcase CMD=status NS=demo               # should print the table above
```

If the namespace is empty, stage it from scratch:

```bash
make dbx-showcase CMD=provision NS=demo
make dbx-showcase CMD=land NS=demo
make dbx-showcase CMD=expectations NS=demo
make dbx-showcase CMD=backfill NS=demo
make dbx-showcase CMD=recon NS=demo
```

## Beat 1 — The history nobody could query (0:00–0:03)

Show the legacy side first: `ls /tmp/otterworks-legacy/sftp-drop/history/2019/`
— fixed-width files, one per month, six years of them, readable only by the
Perl script that produced the finance report.

Then the same data on the lakehouse:

```bash
make dbx-showcase CMD=recon NS=demo
```

Expected: `checks: 49, failed: 0, anomalies expected/actual: 30/30, missing: 0,
unexpected: 0`. Per-year counts and integer-cent totals, per-year quarantine
counts, per-year file counts, and a grand total — all recomputed from the target
tables, not from a fixture, then reruns the transforms to prove idempotency.

The point to make out loud: the legacy parser logged trailer mismatches and
moved on. The 30 quarantined rows are records the old estate silently billed
wrong.

## Beat 2 — Time travel and lineage (0:03–0:06)

```bash
make dbx-showcase CMD=timetravel NS=demo ARGS="--table gold"
make dbx-showcase CMD=lineage NS=demo
```

Time travel prints the Delta version list and the same table as of the previous
version — the answer to "who changed the finance numbers, and what did they look
like before?" The legacy estate's answer was a `.done` folder.

Lineage resolves landing volume → bronze → silver (+ quarantine) → gold with no
manual annotation. Open the Catalog Explorer lineage tab on
`ow_tp.gold.custbill_annual_demo` for the graph; contrast with
`etl/legacy-extra/ops/RESTART_PROCEDURE.doc.txt`, which is what lineage looked
like before.

## Beat 3 — Declared quality and the finance report (0:06–0:08)

The dashboard `ow_tp_billing_history_demo` replaces
`finance_excel_report.pl` (a CSV renamed `.xls`, mailed through a sendmail pipe
that silently no-ops). Annual billed amount by year, quarantine reasons, latest
recon state.

The pipeline `ow_tp_custbill_history_dlt_demo` expresses the quarantine policy
as declared expectations rather than harness code, and agrees with the
harness-built gold table exactly:

```bash
make dbx-showcase CMD=run-pipeline NS=demo    # optional live run; ~5 min, own serverless compute
```

Expected tail: `custbill_dlt_demo: 2856 rows`, `custbill_dlt_annual_demo: 36
rows`, `parity with harness gold: matches`.

## Beat 4 — Databricks catches it, Devin fixes it (0:08–0:12)

Green first, so the failure means something:

```bash
make dbx-showcase CMD=run-job NS=demo
```

Expected: `recon_check: SUCCESS`, `notify_devin: EXCLUDED`.

Now break it the way reality breaks it — a new billing year arrives, is landed
and expected, and the target hasn't absorbed it (use `--kind malformed --period
YYYYMM` for a bad batch instead):

```bash
make dbx-showcase CMD=drift NS=demo ARGS="--kind stale"
make dbx-showcase CMD=run-job NS=demo
```

Expected: `recon_check: FAILED` with a `raise_error` message naming the failing
check ids, and `notify_devin: SUCCESS`; the run's overall state is
`SUCCESS_WITH_FAILURES`, because the notifier task itself succeeded. The notifier is a dependent task with
`run_if: AT_LEAST_ONE_FAILED`; it POSTs job id, run id, run URL, namespace and
base branch to the Devin automation webhook, with the shared secret read from
the `ow_tp` Databricks secret scope.

That webhook starts *OtterWorks billing-history recon failure —
auto-remediate (Databricks)*, which re-runs recon, diagnoses from the failing
check ids, remediates the smallest correct thing, re-runs the anomaly and
idempotency checks, re-triggers the Databricks job to green, and opens one audit
PR. Show the session transcript and the PR side by side: Databricks caught it,
Devin fixed it, the PR proves it.

## Cost controls

Leaving the demo staged is cheap; leaving it *scheduled* is what costs money.

- Serverless SQL only. Never create clusters or extra warehouses — the showcase
  tooling refuses to.
- The warehouse auto-stops after 10 minutes; stopped serverless compute bills
  nothing.
- The recon job schedule is created **PAUSED** and the SQL alert is created
  **PAUSED**. Trigger by Run Now for each take. A polling alert or a live
  schedule will start compute on its own, repeatedly, unattended.
- The declarative pipeline uses its own serverless compute — trigger manually,
  never leave it continuous or scheduled.
- Storage is megabytes at demo scale.
- The remediation automation spawns a real Devin session on every red run; it is
  capped at one concurrent run with an ACU limit. Don't arm a schedule against
  it.

Confirm before walking away:

```bash
make dbx-showcase CMD=status NS=demo    # prints schedule state for job and alert
```

## Teardown

The `demo` namespace is intended to stay staged so history, lineage and the
dashboard stay browsable. Rehearsal namespaces should not:

```bash
make dbx-showcase CMD=teardown NS=<rehearsal-ns>
```

Teardown drops only `ow_tp` objects suffixed with that namespace, then verifies
absence across tables, job, pipeline, alert, dashboard and landed files, exiting
non-zero if anything survived. It never touches unprefixed objects or another
namespace.
