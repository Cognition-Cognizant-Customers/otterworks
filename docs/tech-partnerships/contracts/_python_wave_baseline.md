# Python wave: baseline provenance and local fixtures

Shared preamble for the five 2014-vintage Python cron units. Read
[README.md](README.md) first, then this, then your unit's contract.

## The situation you are inheriting

The CUSTBILL chain runs end-to-end locally, so those three units recon against real
legacy artifacts. The Python crons mostly do not: they name production-shaped resources
that no local fixture provides. This was measured, not assumed — each script was run once
on this VM and the exact failure captured under `/home/ubuntu/tp-golden/python/<unit>/`
(stdout, exit code, manifest). Your unit's contract quotes its result.

Local infrastructure already up (from `make infra-up`, plus one addition):

| Store | Where | Seeded contents (`NS=demo`, `SCALE=demo`) |
|---|---|---|
| Postgres | `localhost:55432`, container `otterworks-postgres-alt` — port 5432 is occupied by a host Postgres that rejects the `otterworks` credentials | schema `otterworks_demo`: 2,000 `documents`, 13,876 `document_versions`, 390 `document_snapshots` |
| DynamoDB (LocalStack) | table `otterworks-file-metadata` | 10,000 file-metadata items, namespace in the `ns` attribute |
| S3 (LocalStack) | `s3://otterworks-data-lake/events/demo/` | 71 hourly gzip JSON event objects, 340,945 bytes |
| MeiliSearch, Redis, LocalStack, Prometheus, Jaeger | `make infra-up` defaults | — |

Seeded by `make seed-legacy NS=demo` and verified with `make seed-legacy-validate NS=demo`
(15/15 checks passed). The seed generators are deterministic per namespace — see
`testdata/legacy/README.md` — so counts and checksums reproduce exactly across reruns, and
`testdata/legacy/manifests/` holds the manifest, including its planted anomalies.

## What your baseline must be, in priority order

1. **Real legacy output.** Try to make the legacy script run by creating the local
   fixture it names (e.g. the LocalStack table/bucket/queue it expects), populated from
   the seeded stores, and by pointing it at the running Postgres. You may create a scratch
   copy of `etl/config.ini` **outside the repo** and point the script at it — you may not
   edit anything under `etl/`, which is the demo's before-state. If the script runs, its
   output is the golden baseline: capture it under `/home/ubuntu/tp-golden/python/<unit>/`
   and recon your converted job against it to the cent.
2. **Seed manifest.** If the legacy script still cannot run, recon your converted job's
   silver/gold output against the deterministic seed manifest (the same counts and
   checksums `testdata/legacy/validate.py` re-derives from the stores) — the source data is
   the same either way, so counts and sums are still verifiable, just not against a legacy
   artifact.
3. **Blocked.** If neither is possible, report `recon_result: blocked` with the exact
   command and the exact error.

**State the tier you used, verbatim, at the top of your recon report** (`baseline: legacy
output` / `baseline: seed manifest` / `blocked`), and say what you had to stand up to get
there. A recon that compares the converted job against itself is worthless; a recon whose
baseline provenance is misstated is worse than worthless. Never synthesize a golden output
and never loosen a comparison to make it pass.
