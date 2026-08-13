---
name: qe-mutation-gate
description: >
  Repo-specific mechanics for running the OtterWorks mutation gate and hardening
  a service's test suite against it. Covers the exact Makefile targets, config
  and baseline locations, onboarded services, report handling, and the
  fail-closed conditions the gate enforces.
---

# Mutation Gate — OtterWorks

This skill provides the repo-specific mechanics that the `!qe-mutation-gate`
Playbook relies on. It is auto-loaded when Devin works in this repository.

## Where everything lives

| Piece | Path |
|---|---|
| Harness | `qe/mutation/harness.py` (stdlib + PyYAML; run via the Makefile) |
| Service onboarding config | `qe/mutation/config.yaml` |
| Committed baseline ledgers | `qe/mutation/baselines/<service>.json` |
| Reports (git-ignored CI artifacts) | `qe/reports/mutation-<service>.{json,md}` |
| Overview doc | `qe/README.md` |

## Commands

```bash
# One-time: install the service's test dependencies into its local .venv
make qe-mutation-setup SERVICE=search-service

# Run the gate (verifies clean suite, runs mutants, compares to baseline)
make qe-mutation SERVICE=search-service

# Audited rebaseline — only after killing survivors; reason is recorded
make qe-mutation-baseline SERVICE=search-service \
  REBASELINE_REASON="killed 5 auth-middleware survivors with negative-path token tests"
```

The Makefile runs the harness with `uv run --with pyyaml==6.0.2`, so no global
Python setup is needed beyond `uv`.

## Onboarded services

- **`search-service`** — `status: active`. Python/FastAPI. Source globs
  `app/**/*.py`; suite runs `.venv/bin/python -m pytest -q -x` inside
  `services/search-service`. Deterministic seed `20260813`, mutant cap `60`.
- **`document-service`** — `status: onboarding-blocked`. Its clean suite is red
  on `main`: `tests/test_documents_api.py` calls authenticated document
  endpoints without `Authorization` headers after the JWT identity-hardening
  changes. Repairing that suite (adding proper auth headers/fixtures, **not**
  weakening the service's auth) is the onboarding work; the gate refuses to run
  until the suite is green.

To onboard another service, add an entry to `qe/mutation/config.yaml`
(`status: active`, `dir`, `source_globs`, `test_cmd`, `timeout_seconds`,
`mutant_cap`, `seed`), verify its clean suite is green, then create the initial
baseline with an audited reason.

## How the gate decides PASS/FAIL

Fails closed on any of:

1. Clean suite red (mutation results are meaningless on a failing suite).
2. No baseline ledger for the service.
3. Fingerprint mismatch — the SHA-256 fingerprint covers the service's source
   files, the harness itself, and the service's config block, so *any* change
   to inputs that affect recorded behavior invalidates the baseline.
4. A survivor not in `allowed_survivors` (a new coverage hole).
5. A baseline entry for a mutant that is now killed (stale ledger — the
   baseline must ratchet down).

Mutant selection is deterministic: same source + seed + cap → the same mutant
set, so runs are reproducible across sessions and CI.

## Reports and PR evidence

`qe/reports/` is git-ignored on purpose — generated reports churn diffs. Treat
the report as a CI artifact: paste the summary block of
`qe/reports/mutation-<service>.md` (result, mutants run/killed/survived, and
the surviving-mutant delta) into the PR body as evidence.

## Test-suite conventions (search-service)

- Tests live in `services/search-service/tests/`, pytest with fixtures in
  `conftest.py`; run them directly with
  `cd services/search-service && .venv/bin/python -m pytest -q`.
- The suite must stay green against unmutated source — the gate re-verifies
  this on every run.

## Branch / isolation rules

- `main` is the golden before-state. All hardening work happens on a working
  branch; never push baseline changes or new tests to `main` directly.
- The gate mutates source files **in place and restores them** (original bytes
  restored in a `finally` block). Run it on a clean working tree so a crash
  can be detected and reverted with `git status` / `git diff`.

## Reverting a run

Everything the gate writes is either restored automatically (mutated sources)
or git-ignored (reports). The only committed state is the baseline ledger and
any tests you added — revert by dropping the branch.

## Constraints

- Do not fix the known planted bug in
  `services/admin-service/config/environments/production.rb` — it is a
  deliberate lab feature, not a target for this gate.
- Do not weaken document-service's JWT auth to make its suite green; fix the
  stale tests instead.
- Baseline changes are audited: `REBASELINE_REASON` is required and recorded in
  the ledger.
