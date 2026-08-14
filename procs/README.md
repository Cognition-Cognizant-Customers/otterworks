# Stored-procedure recording loop

The `procs/` directory contains the declarative scenarios and immutable
recordings for the legacy billing application. The recordings are made against
the running PostgreSQL stack, not against a Python reimplementation.

## Loop

```bash
make procs-up NS=dev
make procs-list
make procs-record NS=dev
make procs-rules-gate MODULE=plans
make procs-parity NS=dev
cd frontend/client-app && npm run dev
make procs-down NS=dev
```

The Makefile derives the Postgres and HTTP host ports from `NS`, so separate
namespaces can run concurrently. Use `OUTPUT_DIR` when recording isolated
fixtures for a namespace-specific verification run.

The client Vite server proxies `/billing-api/*` to the extracted service. With
the default `NS=dev` stack, run the client command above and open
`http://localhost:3000/billing/plans`; no billing URL environment variable is
required. Vite dev enables the billing routes by default. A preview build
requires the explicit fixture flag:

```bash
VITE_ENABLE_BILLING_FIXTURE=true npm run build
npm run start -- --host 127.0.0.1 --port 4173
```

The `/billing-api` proxy is used by the dev server and by this explicitly
flagged preview. Builds without the flag leave the routes unregistered, as do
production builds. These screens are part of this local parity fixture, not
the deployed application. For another namespace, set `BILLING_SERVICE_URL` to
that namespace's derived target port when starting Vite.

Each scenario resets the `billing` schema to the checked-in schema, procedure
definitions, and seed. The recorder invokes the declared entrypoint, captures
the selected result fields, runs the named state probes, and writes one JSON
transcript under `procs/transcripts/<module>/`.

Transcripts are immutable. Re-recording requires `--allow-rerecord` and a
changed procedure source hash. If only the harness changed, use the explicit
audited escape `--allow-rerecord --rerecord-reason harness-change`; if the
scenario/probe design changed, use
`--allow-rerecord --rerecord-reason scenario-redesign`. Each resulting
transcript records that reason. The namespace affects only the running
database; it is not written into transcript content.

Recording an existing checkout is intentionally refused unless the procedure
source changed. Through Make, the normal command is:

```bash
make procs-record NS=dev
```

For a procedure-source change, explicitly authorize the refresh:

```bash
make procs-record NS=dev ALLOW_RERECORD=1
```

For a harness-only normalization or recorder change, use the auditable reason:

```bash
make procs-record NS=dev ALLOW_RERECORD=1 RERECORD_REASON=harness-change
```

For a scenario/probe redesign with unchanged procedures, use:

```bash
make procs-record NS=dev ALLOW_RERECORD=1 RERECORD_REASON=scenario-redesign
```

Each non-procedure reason is valid only for the named change; neither is a
substitute for recording changed procedures.

## Add a scenario

1. Add a YAML file under `procs/scenarios/<module>/`.
2. Set `id`, `module`, `description`, `entrypoint`, and `kind`.
3. Declare typed `inputs`.
4. Select returned `fields`, or use `capture_query` for a side-effecting
   procedure.
5. Add named `probes` with stable SQL queries.
6. Add stable `rules` identifiers for the later rules gate.
7. Run `make procs-record NS=<namespace>` against a fresh namespace and inspect
   the resulting transcript.

Scenario SQL should observe the legacy state. It should not duplicate
procedure logic in Python.

## Extraction verification

`routes.yaml` maps extracted legacy entrypoints to target HTTP endpoints. The
replay harness resets the target before each scenario, replays only modules
marked `extracted`, and records semantic field/probe comparisons in
`procs/reports/parity.md` and `parity.json`. Pending modules are reported as
`SKIP`, never as a pass. Replay also checks that the transcript source hash
still matches the checked-in procedure files.

The rules gate is a separate human-approval check:

```bash
make procs-rules-gate MODULE=plans
```

It validates the ledger decision, scenario coverage, source ranges, and
target-test markers before parity is allowed to grade an extracted module.

## Oracle parity (migration evidence)

The Oracle billing estate (`services/legacy-billing/db/oracle/`, PDB
`FREEPDB1`, schema `OW_BILLING`, `localhost:52521`) is a contractual PL/SQL
port of the golden Postgres legacy-billing procedures. The Oracle parity loop
replays the same declarative scenario set (`procs/scenarios/`) against both
estates and grades every one of the 12 documented entrypoints, producing the
evidence demos use to show the port is semantically equivalent before a
migration.

```bash
make procs-up NS=dev            # golden Postgres estate
make oracle-billing-up          # Oracle estate (first pull is slow)
make oracle-parity NS=dev       # seed both, replay both, grade
make procs-down NS=dev
make oracle-billing-down
```

`make oracle-parity NS=<ns>`:

1. Seeds the Oracle estate deterministically for the namespace via the
   existing `oracle-billing-seed` seeder (idempotent, namespace-scoped).
2. Records a fresh Postgres run with the existing recorder
   (`procs/harness/record.py`), which rebuilds `billing_<ns>` from the
   checked-in schema, procedures, and seed before every scenario.
3. Records a fresh Oracle run with the Oracle driver
   (`procs/harness/oracle_record.py`), which resets only the static-seed
   tenants (namespace-seeded rows survive) and calls each `pkg_*` entrypoint
   through `python-oracledb` — `SYS_REFCURSOR` functions are fetched like
   Postgres set-returning functions, procedures run their translated capture
   query and probes afterwards.
4. Compares the two runs field-by-field and probe-by-probe
   (`procs/harness/oracle_parity.py`) and writes
   `procs/reports/oracle-parity.md` and `oracle-parity.json`: a pass/fail
   rollup per entrypoint, per-scenario results, and row/value diffs on any
   divergence. A non-green report exits non-zero. The comparator also warns
   if the fresh Postgres run drifts from the immutable golden transcripts in
   `procs/transcripts/`.

The Oracle side of the contract is declarative:

- `procs/oracle/oracle_map.yaml` — the Postgres→Oracle entrypoint mapping
  (from the estate README) plus per-scenario Oracle translations of capture
  and probe SQL (`TO_CHAR` formatting, `DECODE` over the `*_CD`
  magic-number codes, `pkg_ow_util.f_md5_uuid` for `md5(...)::uuid`).
- `procs/oracle/transcripts/` — immutable Oracle transcripts recorded with
  `make oracle-record`, fingerprinted with `ORACLE_SOURCE_SHA` over the
  Oracle estate SQL. Like the Postgres transcripts, they refuse to be
  re-recorded unless the estate source changed (`ALLOW_RERECORD=1`).

Both existing loops are untouched: `make procs-*` behaves exactly as before,
and the extraction parity report (`procs/reports/parity.*`) is separate from
the Oracle report.
