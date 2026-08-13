# Playbook: Harden a Test Suite with the Mutation Gate

> **Facilitator / author:** this file is the source for a **Devin Playbook**.
> Copy its contents into your Devin organization (Settings → Playbooks → *Create
> a new Playbook*) so sessions can invoke it as `!qe-mutation-gate`. See
> [Creating Playbooks](https://docs.devin.ai/product-guides/creating-playbooks).

## Overview

Use this playbook to **measure and improve whether a service's test suite can
actually catch bugs**. The mutation gate plants deterministic, realistic bugs
(mutants) into the service's source — a flipped comparison, a swapped `and`/`or`,
an off-by-one arithmetic change — and runs the service's own test suite against
each one. A mutant the suite fails to kill is a **proven test-coverage hole**:
the suite cannot tell the buggy program from the correct one. Coverage percent
tells you which lines *ran*; the mutation gate tells you which behaviors are
*asserted*.

The guiding principle: **a test suite's quality is measured by the bugs it can
catch, not by the lines it executes.** Every surviving mutant is a concrete,
reproducible bug the current suite would ship. The work of this playbook is to
kill survivors with real assertions and ratchet the committed baseline down —
never to relax the gate.

## Required from user

- **The service** — which service's suite to harden (must be onboarded in the
  repo's mutation config; the repo Skill lists the onboarded services and exact
  commands).
- **The kill budget** — how many surviving mutants to eliminate in this run
  (e.g., "kill 5 survivors in the auth middleware"). Small, verifiable
  increments beat a boil-the-ocean pass.
- **Scope preference (optional)** — a file or module to prioritize (e.g., focus
  on `middleware/auth.py` survivors before formatting helpers).

## Procedure

1. **Run the gate on a clean checkout** to establish the before-state. The gate
   first verifies the clean suite is green (mutation results are meaningless on
   a red suite), then runs the deterministic mutant set and compares survivors
   against the committed baseline ledger. Expect PASS: the baseline records the
   currently-known survivors.

2. **Pick the survivors to kill.** Read the report's surviving-mutant list. Each
   entry is `file:line:col:operator`. Prioritize survivors in
   security-sensitive or business-critical code (auth checks, money math,
   pagination, state machines) over logging/formatting code.

3. **Understand each mutant before writing a test.** Open the source location
   and work out what observable behavior the mutation changes. The test you
   write must assert *that behavior* — a test that kills the mutant by accident
   (import error, incidental assertion) is a weak test that will rot.

4. **Write the killing test in the service's own suite**, following the suite's
   existing conventions and fixtures. One focused test can kill several related
   survivors on the same branch/condition.

5. **Re-run the clean suite** and confirm it is green — the new tests must pass
   against the *unmutated* source.

6. **Re-run the mutation gate.** It will now FAIL with "stale baseline entry
   (mutant now killed)" for every survivor you eliminated. This is the gate
   working: the ledger must ratchet down to match reality.

7. **Rebaseline with an audited reason** stating what was killed and how (e.g.,
   "killed 5 auth-middleware survivors with negative-path token tests"). The
   reason is recorded in the ledger and reviewed on the PR.

8. **Re-run the gate and confirm PASS** with the smaller baseline. Paste the
   report summary (mutants run / killed / survived, and the delta from the
   before-state) into the PR body — the report file itself is a git-ignored CI
   artifact.

9. **Open a PR** containing the new tests and the ratcheted baseline. Devin
   Review will comment on the PR; resolve its findings before handoff.

## Specifications (postconditions)

A run is complete when:

- [ ] The clean suite is green with the new tests against unmutated source
- [ ] The mutation gate PASSes with a strictly smaller `allowed_survivors` list
- [ ] Every removed baseline entry corresponds to a mutant now killed by a real
      assertion (not by a flaky or incidental failure)
- [ ] The rebaseline reason in the ledger says what was killed and how
- [ ] No mutation operators, seeds, caps, or gate logic were changed
- [ ] The PR body contains the before/after gate summary as evidence
- [ ] Devin Review comments on the PR are addressed

## Worked example: the auth-middleware survivor

A real run against a search service reported this survivor:

```
SURVIVED  app/middleware/auth.py:51:15:bool-And (And -> Or)
SURVIVED  app/middleware/auth.py:51:25:cmp-Eq (Eq -> NotEq)
```

Line 51 guards service-to-service authentication:

```python
if svc_key and svc_key == expected_key:
```

Both mutants survived — meaning the suite never exercises the internal-key
path at all. With `and → or`, **any non-empty key** (or an empty expected key)
authenticates; with `== → !=`, only *wrong* keys authenticate. Either mutation
is an authentication bypass the test suite would ship silently.

The fix is a pair of negative-path tests: one asserting a request with a wrong
internal key is rejected, one asserting the correct key is accepted. Re-running
the gate flips both mutants to `killed`, the gate fails closed on the now-stale
baseline entries, and the audited rebaseline ratchets the ledger down by two.

The point: line coverage could be 100% here and still miss this — the line runs,
but nothing asserts the *decision*. The mutation gate turned "our auth tests
feel thin" into a named, reproducible bug with a one-command proof.

## Advice

- Kill survivors in small batches (3–7) per PR. Small ratchets review cleanly
  and keep the audit trail meaningful.
- If two survivors sit on the same condition (like the `bool-And` + `cmp-Eq`
  pair above), one well-aimed test usually kills both — verify the report
  confirms it.
- Survivors in config/constant definitions (e.g., a default flag) often need a
  behavioral test of the *consumer* of the flag, not an assertion on the
  constant itself.
- If the gate reports a **fingerprint mismatch**, source or config changed since
  the baseline was recorded. Inspect the diff first; rebaseline only when the
  change is legitimate, with a reason that names it.
- A service can be `onboarding-blocked` in the config when its clean suite is
  red. Repairing that suite *is* the onboarding work — never mutate a service
  whose clean suite fails.
- This procedure runs well unattended: a scheduled session per service on a
  weekly cadence, each with a small kill budget, ratchets the whole estate down
  over time.

## Forbidden actions

- Do **not** grow the `allowed_survivors` list or add entries to make the gate
  pass — the baseline only ratchets down.
- Do **not** change mutation operators, the seed, the mutant cap, or the gate's
  fail-closed logic to alter results.
- Do **not** delete or weaken existing tests to make the clean suite green.
- Do **not** write tests that kill mutants by accident (e.g., asserting on log
  strings or import side effects) — assert the changed behavior.
- Do **not** rebaseline without a specific, audited reason.
- Do **not** run the gate against a service marked `onboarding-blocked`; fix its
  clean suite first, as its own unit of work.
