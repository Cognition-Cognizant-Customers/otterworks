# Playbook: Close Edge-Case Test Coverage Gaps (Positive/Negative/Boundary)

> **Facilitator / author:** this file is the source for a **Devin Playbook**.
> Copy its contents into your Devin organization (Settings → Playbooks → *Create
> a new Playbook*) so sessions can invoke it as `!edgecase-tests`. See
> [Creating Playbooks](https://docs.devin.ai/product-guides/creating-playbooks).

## Overview

Use this playbook to strengthen a service's test suite with **positive,
negative, and boundary edge-case tests** — and to *prove* the new tests have
teeth, not just coverage. The proof comes from a mutant harness: a catalog of
realistic planted bugs. A test suite with real edge-case coverage kills every
mutant (at least one test fails while the bug is planted); a mutant that
survives is a demonstrated coverage gap.

The guiding principle: **a test only counts if it fails when the code is
wrong.** Line coverage is not the goal; killed mutants are. Never weaken,
delete, or special-case a mutant to make the run green — the catalog is the
source of truth for what the suite must catch.

## Required from user

- **The target repo and service** — e.g. `otterworks`, `document-service`.
- **The mutant harness location** — where the catalog and runner live (in
  OtterWorks: `qa/edgecase/`, driven by `make edgecase-verify`). If the repo
  has a Skill for the harness, follow it for exact commands.
- **Scope** (optional) — a single mutant id or category to focus on.

## Procedure

1. **Run the harness on the untouched suite** to get the ground truth:
   which mutants are KILLED and which SURVIVED. Record the survivor list —
   this is the work queue.

2. **For each survivor, understand the gap before writing a test.** Read the
   mutant's `original`/`mutated` pair and the code around it. State, in one
   sentence, the input class the suite is missing (e.g. "no test searches with
   a `%` wildcard", "no test PATCHes with an empty body").

3. **Write the missing test in the service's existing test file and style.**
   Match the existing fixtures, naming, and assertion patterns. Each test must
   assert the *correct* behavior — derived from the unmutated code and the
   service contract — never from what the mutant happens to do.

4. **Prove each test kills its mutant.** Run the harness for that mutant id.
   If it still survives, the test is asserting too little; tighten the
   assertions rather than adding more tests.

5. **Prove the suite is still green on the real code.** The full service test
   suite must pass with no mutants planted. A test that only passes against
   mutated code is wrong.

6. **Finish with a full harness run** showing every mutant KILLED, and include
   that output in the PR description alongside a survivor-by-survivor summary
   of the edge cases that were missing.

## Specifications (postconditions)

- `make edgecase-verify` (or the repo's equivalent) exits green: **all mutants
  KILLED**, including every mutant that survived at the start.
- The service's own test suite passes unmodified code.
- No changes to application code, the mutant catalog, or the harness — only
  test files change. (If a mutant reveals a *real* bug in the application,
  stop and report it instead of testing around it.)
- New tests are grouped and named so a reader can tell which edge case each
  one pins down (positive / negative / boundary / contract).
- The PR description contains the before (survivor list) and after (all
  killed) harness output.

## Worked example: a survivor the suite missed

On OtterWorks `document-service`, the initial harness run reported
`EDGE-LIST-PAGE-OFFSET` SURVIVED: the mutant changes the pagination offset
from `(page - 1) * size` to `page * size`, which silently skips the entire
first page of results. The existing test seeded three documents and asserted
only the *total count* — which the mutated query still returns correctly —
so the suite stayed green with a live off-by-one-page bug. The fix was a
boundary test that requests `page=1, size=2` and asserts the *returned items*,
not just the total; with that test in place the mutant is killed.

## Advice

- Work survivor by survivor and re-run the harness per mutant (`MUTANT=<id>`)
  for a fast loop; save the full run for the end.
- Negative-input survivors (wildcards, markup injection, missing ids) usually
  need an *adversarial* input in the test, not more happy-path data.
- Contract survivors (idempotence, version bumps) usually need an assertion on
  a field the existing tests ignore — assert what must *not* change, too.
- If two mutants share one missing input class, one well-aimed test may kill
  both; prefer that over mechanical one-test-per-mutant padding.

## Forbidden actions

- Do **not** modify application code, the mutant catalog, or the harness.
- Do **not** derive expected values from mutated behavior — the unmutated code
  and the service contract are the source of truth.
- Do **not** pad the suite with assertion-free or snapshot-everything tests to
  chase line coverage; every new test must kill at least one survivor.
- Do **not** mark a run complete while any mutant survives without an explicit
  report explaining why.
