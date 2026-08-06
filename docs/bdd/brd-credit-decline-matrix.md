# BRD Credit-Decline Rules — Test Matrix (worked external example)

**Source BRD:** `Credit_Score_Decline_Rules_BRD.docx` — Agency Portal (AP) → Guidewire PolicyCenter
(GWPC), Massachusetts Home lines, auto-decline on credit score.
**Companion:** [`decision-table-testing-standard.md`](./decision-table-testing-standard.md) — the
house format this document instantiates, plus the repo-wide threshold register (§7 there).

---

## 0. Read this first: scope and provenance

**None of this is implemented in OtterWorks.** A full-text search of the repository for
`guidewire|policycenter|declination|credit score|agency portal|underwriting` returns zero matches.
There is no Agency Portal, no policy type, no credit bureau integration and no declination notice
anywhere in the codebase.

So this document is **not** a coverage gap and **must not** be turned into product code by a test
package. It exists for two reasons:

1. **It is the reference worked example** for
   [`decision-table-testing-standard.md`](./decision-table-testing-standard.md). The BRD is a
   textbook decision table: numeric thresholds with an ambiguous edge, several scope dimensions
   that must *not* trigger the rule, and a four-quadrant outcome matrix across two systems. That is
   the exact shape of OtterWorks' own rules (`MAX_UPLOAD_BYTES`, `used_bytes >= quota_bytes`, the
   gateway rate limiter, every pagination clamp) and the exact shape its tests are missing.
2. **It is a ready-to-run matrix** if the credit-decline rule engine is ever actually built — as a
   product feature with its own scope, not as part of the test-coverage program. The cases below
   are derivable from the BRD today and are reusable against whatever implements it: a real Agency
   Portal, a mock, or an OtterWorks demo variant.

If you are executing a test work package (WP-01 … WP-24), the deliverable you want is the
**standard**, not this matrix. Use this document as the example of a completely filled-in table.

---

## 1. The rules under test

Two rules, same shape, different thresholds, keyed on policy type.

### R1 — HO6 credit decline

**Condition:** `credit_score < 590`
**Scope (all must hold):** Source of Quote = **Agency Portal** · State = **MA** · LOB = **Home** ·
Policy Type = **HO6** · Transaction = **New Business**
**Outcome when true:** quote declined in AP; declination notice **sent to** GWPC; declination notice
**generated in** GWPC.

### R2 — HO4 credit decline

**Condition:** `credit_score < 580`
**Scope:** identical to R1 except Policy Type = **HO4**.
**Outcome when true:** as R1.

**Input dimensions**

| Dimension | Type | In-scope values | Out-of-scope values |
|---|---|---|---|
| Credit score | numeric | 0 … 850 (see E2) | non-numeric, null, missing (see E1/E3) |
| Source of Quote | enum | Agency Portal | Direct, Call Center |
| State | enum | MA | CT, NH, RI, all others |
| LOB | enum | Home | Auto, all others |
| Policy Type | enum | HO4, HO6 | HO3, HO5, all others |
| Transaction | enum | New Business | Endorsement, Renewal, Rewrite |
| Entry point | enum | AP → GWPC, GW direct | — |

**Expected outcome — per downstream system**

| System | Rule fires | Rule does not fire |
|---|---|---|
| Agency Portal | quote **Declined** | quote proceeds / issues |
| GWPC — notice **sent** | yes (notice transmitted from AP) | no |
| GWPC — notice **generated** | yes (notice document created in GWPC) | no |
| Any other downstream | nothing | nothing |

"Sent" and "generated" are **separate observations of two different systems** and must be asserted
separately — see the G1 quadrant in §4, which is precisely the case where they diverge.

---

## 2. Boundary trio per rule (cases B1-B8)

The BRD's core risk is `<` versus `<=`. The `limit` case is what pins it.

| # | Rule | Policy type | Credit score | AP outcome | Notice sent to GWPC | Notice generated in GWPC |
|---|---|---|---|---|---|---|
| B1 | R1 | HO6 | 589 | Declined | Yes | Yes |
| B2 | R1 | HO6 | **590** | **Not declined** | No | No |
| B3 | R1 | HO6 | 591 | Not declined | No | No |
| B4 | R2 | HO4 | 579 | Declined | Yes | Yes |
| B5 | R2 | HO4 | **580** | **Not declined** | No | No |
| B6 | R2 | HO4 | 581 | Not declined | No | No |
| B7 | R1 | HO6 | 579 | Declined | Yes | Yes |
| B8 | R2 | HO4 | 585 | **Not declined** | No | No |

- **B2 and B5** are the mandatory `limit` cases. They are the only cases that distinguish
  `< 590` from `<= 590`. Do not drop them.
- **B7** is HO6 evaluated at HO4's threshold: it must still decline. Proves the two thresholds are
  not swapped.
- **B8** is HO4 evaluated at a score that *would* decline under HO6: it must **not** decline.
  Together B7+B8 prove per-policy-type routing rather than one global threshold.

Every B-case with "Declined = Yes" must additionally assert **exactly one** declination notice —
not one per retry, not one per downstream consumer (see E7).

---

## 3. Scope negatives (cases N1-N6)

Each varies **one** dimension out of scope, holds every other dimension at a firing value, uses a
credit score of 500 (deep in decline territory), and expects **not declined, no notice sent, no
notice generated, no side effect anywhere**.

| # | Varied dimension | Value | Score | Expected |
|---|---|---|---|---|
| N1 | Source of Quote | Direct / Call Center (not Agency Portal) | 500 | not declined, no notice |
| N2 | State | CT, NH, RI (any non-MA) | 500 | not declined, no notice |
| N3 | LOB | Auto (not Home) | 500 | not declined, no notice |
| N4 | Policy Type | HO3, HO5 (not HO4/HO6) | 500 | not declined, no notice |
| N5 | Transaction | Endorsement, Renewal, Rewrite (not New Business) | 500 | not declined, no notice |
| N6 | Combination | Agency Portal + MA + Home + HO6 + **Renewal** | 500 | not declined, no notice |

N6 is the combination negative required by the standard: everything is in scope except one value
buried in the middle. It is the case that catches an `&&` refactored into an `||`.

Asserting "the quote issued" is **not sufficient** for N1-N6. Each must assert the absence of the
side effect in GWPC as well — a rule that declines nothing but still emits a notice is a defect the
response body will not show you.

---

## 4. Guidewire-side quadrants (cases G1-G4)

From BRD §5.3 / §5.4. The asymmetry between G1 and G3 is the whole point of this block: the same
underlying criteria produce a *different observable outcome* depending on the entry point.

| # | Entry point | Criteria met | Expected |
|---|---|---|---|
| G1 | GW direct | yes | Quote **blocked**, **no** declination notice generated |
| G2 | GW direct | no | Policy issued successfully, no notice |
| G3 | AP → GWPC | yes | Declined in AP, notice **sent and generated** in GWPC |
| G4 | AP → GWPC | no | Issued, no notice anywhere |

G1 blocks without generating a notice; G3 declines *and* generates one. A test suite that asserts
only "was it declined?" passes with G1 and G3 implemented identically — and that would be wrong.
This is the concrete reason the standard requires outcomes to be listed per downstream system.

**Total: 8 boundary rows + 6 scope-negative rows + 4 quadrants = 18 enumerated rows, which expand
to 24 executable cases.** The N-rows are multi-valued: N1 names two out-of-scope sources, N2 three
states, N4 two policy types, N5 three transactions, N3 and N6 one each — 12 negatives once each
value gets its own case. 8 + 12 + 4 = 24. SOW §4b quotes the 24 figure against the 18 rows; the
expansion above is the reconciliation. Do not collapse a multi-valued row into one parametrised
case that stops at the first value.

---

## 5. Open questions for the business analyst (E1-E10)

The BRD does not specify these. Each is a real branch that some implementation will pick silently
if nobody decides. **Do not guess.** Each becomes a test case once answered; until then, if an
implementation exists, pin its current behavior with a case named
`..._current_behavior_pending_decision` so a deliberate change turns it red.

| # | Case | Open question |
|---|---|---|
| E1 | Credit score **missing / no-hit / thin file** | Decline, refer, or proceed? |
| E2 | Credit score `0`, negative, or `> 850` | Reject as invalid input, or evaluate? |
| E3 | Score returned as string / null / non-numeric | Hard error, or treat as no-hit? |
| E4 | Credit bureau service timeout or 5xx | **Fail-open (issue) or fail-closed (decline)?** Must be explicit. |
| E5 | Two applicants (co-applicant) on one HO6 quote | Which score governs — primary, lowest, or average? |
| E6 | Score changes between quote and bind | Re-evaluate at bind, or honor the quote-time decision? |
| E7 | Duplicate submit of the same quote | Exactly one declination notice (idempotency) |
| E8 | MA-frozen / consumer-blocked credit file | Legal path differs from a low score |
| E9 | Test-data trigger (BRD §4: first name + last name + DOB + address) mismatch | A stale test identity silently produces the wrong band — the test data itself needs a fixture-integrity assertion |
| E10 | Notice generated in GWPC but AP transaction rolls back | No orphaned declination notice |

E4 is the one that must never be left open: an unanswered fail-open/fail-closed question means the
system's behavior under bureau outage is whatever the exception handler happened to do.

E7 and E10 are the two-system consistency cases. They cannot be tested from the AP side alone.

---

## 6. The identity → band fixture bank is itself a tested precondition

**BRD §4 note.** The credit band is not an injectable score. It is triggered by **test-data
identity** — first name + last name + date of birth + address. A designated identity maps to a
designated credit band; change any part of the identity, or let the upstream test-data set drift,
and the quote silently lands in a different band.

The consequence: **every one of the cases above depends on a maintained identity → band fixture
set**, and none of them fails loudly when that set rots. A case expecting 589 (decline) that
silently receives 620 will report "not declined" and look like a product bug, or worse, will be
"fixed" by relaxing the assertion.

Whoever owns this rule must therefore treat **"the fixture bank is correct and current"** as a
tested precondition, not an assumption:

- a smoke test asserting that each designated identity still returns its intended band,
- run **before** the decision-table suite, in the same pipeline,
- failing the run loudly and distinguishably (`fixture bank drift`, not `rule failed`).

**OtterWorks already has this machinery.** The same pattern — generated fixtures plus an
independent validator that asserts the fixtures still satisfy their stated criteria — is
implemented in:

- `testdata/harness/validate.py` — the validator,
- `make testdata-validate NS=<ns>` (`Makefile:160`) — the invocation, namespaced per data set,
- `testdata/harness/create_schema.sql` — the schema the fixtures are checked against.

A credit-band fixture bank should be expressed the same way: identities and their intended bands as
the data set, a criteria file asserting the mapping, and `testdata-validate` as the precondition
gate. Any test package in this repo that depends on generated fixtures should do likewise.

---

## 7. Applying this shape to OtterWorks' real rules

The mapping from the BRD's concepts to rules that *do* exist in this repo — this is how the BRD gets
addressed without inventing an insurance feature. Full file:line detail and work-package ownership
for each is in [`decision-table-testing-standard.md`](./decision-table-testing-standard.md) §7.

| BRD concept | OtterWorks rule with the same shape | Coverage today |
|---|---|---|
| `Credit Score < 590` → decline | `MAX_UPLOAD_BYTES` (100 MB) → reject upload (`services/file-service/src/handlers.rs:87`, limit at `config.rs:49-52`) | untested |
| Rule differs by Policy Type (HO4/HO6) | Share permission tier changes allowed actions (`SharePermission::from_str_value`, applied at `services/file-service/src/metadata.rs:756`) | one parse test (`metadata.rs:882`), no matrix |
| Threshold breach → decline + notice | `used_bytes >= quota_bytes` → over quota (`services/admin-service/app/models/storage_quota.rb:27`) | boundary untested |
| Rate / eligibility gate | Gateway token-bucket rate limiter, `RATE_LIMIT_RPS` (`services/api-gateway/internal/config/config.go:75`, admission at `internal/middleware/ratelimit.go:62`) | refill and boundary untested |
| Notice **sent** vs. **generated** (two systems) | Event fan-out: action in service A → notification / audit record in service B | three `side_effect` tests in `tests/api`, never executed in CI |

Note the operator difference between rows one and three: `>` for the upload limit (the limit is
accepted) and `>=` for the quota (the limit is already over). Two byte-count thresholds in the same
product with opposite edge semantics is exactly the situation the mandatory `limit` case exists to
document.

---

## 8. What a worker should take from this document

1. Copy the **rule record** structure from §1: dimensions table, condition copied verbatim from
   source with its operator, outcomes per downstream system.
2. Copy the **boundary trio** from §2 — including the middle case.
3. Copy the **scope negatives** from §3 — one dimension varied at a time, plus one combination, each
   asserting no side effect anywhere.
4. Copy the **quadrant** idea from §4 wherever a rule spans two systems: assert both, separately.
5. Copy the **open-questions** discipline from §5: unanswered branches get written down and pinned,
   never guessed.
6. Copy the **fixture-integrity precondition** from §6 whenever your cases depend on generated data.
