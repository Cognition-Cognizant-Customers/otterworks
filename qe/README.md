# qe/ — Quality-engineering controls

Programmatic controls that measure whether the test suites can actually catch
bugs, and gate changes on that evidence.

## Mutation gate

`qe/mutation/harness.py` enumerates deterministic AST-level mutants of a
service's source (comparison flips, boolean swaps, arithmetic swaps, boolean
constant flips, `not` removal), runs the service's own test suite against each
mutant, and compares the surviving set against a committed baseline ledger in
`qe/mutation/baselines/<service>.json`. A surviving mutant is a proven
test-coverage hole: the suite cannot tell the mutated program from the real one.

```bash
make qe-mutation SERVICE=search-service          # run the gate
make qe-mutation-baseline SERVICE=search-service REBASELINE_REASON="..."  # audited rebaseline
```

The gate fails closed on:

- a survivor that is not in the baseline (a new coverage hole),
- a baseline entry that is now killed (stale ledger — ratchet it down),
- a source fingerprint mismatch (source, config, or harness changed since the
  baseline was recorded),
- a red clean suite (mutation results are meaningless on a failing suite).

Every baseline change goes through `--rebaseline --reason`, and the reason is
recorded in the ledger. Reports land in `qe/reports/` (git-ignored — they are
CI artifacts; paste the summary into the PR body as evidence).

Services are onboarded in `qe/mutation/config.yaml`. A service whose clean
suite is red is recorded as `onboarding-blocked` with the reason, and the gate
refuses to run for it until the suite is repaired.
