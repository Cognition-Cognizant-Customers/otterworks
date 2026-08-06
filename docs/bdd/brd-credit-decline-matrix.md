# Worked example — credit-score auto-decline BRD as a decision table

**Companion to:** [`decision-table-testing-standard.md`](./decision-table-testing-standard.md).
**Status:** reference material. **Not** an OtterWorks feature.

## 0. Why this document exists, and what it is not

This is the worked example for the house decision-table standard. It transcribes an
insurance-domain business requirements document — Agency Portal → Guidewire PolicyCenter,
Massachusetts Home new-business quotes auto-declined on credit score — into the full 24-case test
matrix the standard demands.

**None of this domain exists in OtterWorks.** A full-text search of the repo for
`guidewire|policycenter|declination|credit score|agency portal|underwriting` returns zero matches.
So the BRD cannot be "covered" by adding tests to this codebase, and nothing here should be read as
a request to build an insurance rules engine.

What it *is*: a textbook decision table with numeric thresholds, per-category routing, and a
four-quadrant per-system outcome matrix — exactly the shape OtterWorks' own rules have (upload size
caps, storage quotas, rate limits, pagination clamps, tenant TTLs) and exactly where the coverage
inventory found the repo thinnest. Read it as the reference for how a threshold rule should be
enumerated before a line of test code is written; §8 of the standard maps every real OtterWorks
threshold to the WP that owes it the same treatment.

If the intent ever becomes to actually build the rule engine, this matrix is the pre-built
acceptance suite and is reusable against whatever implements it.

---

## 1. The rule, in the template's form

```
Rule id:        R1 (HO6) and R2 (HO4)  — one id per policy type, per standard §2.2
Source:         BRD "Credit Score Decline Rules", §5.1-§5.4 (+ §4 test-data identity note)
Owning WP:      n/a — no OtterWorks implementation exists

Dimensions:
  D1 Credit score            values: 0..850, plus no-hit / thin-file / missing   scoping? no
  D2 Policy Type             values: HO3, HO4, HO5, HO6                          scoping? yes
  D3 Source of Quote         values: Agency Portal, Direct, Call Center, GW direct scoping? yes
  D4 State                   values: MA, CT, NH, RI, ...                          scoping? yes
  D5 Line of Business        values: Home, Auto, ...                              scoping? yes
  D6 Transaction type        values: New Business, Endorsement, Renewal, Rewrite  scoping? yes

Condition:      R1: Policy Type = HO6 AND credit score < 590
                R2: Policy Type = HO4 AND credit score < 580
                both gated on: Source = Agency Portal AND State = MA
                               AND LOB = Home AND Transaction = New Business

Expected outcome, per downstream system:
  S1 Agency Portal (AP)      -> quote declined / not declined
  S2 notice SENT to GWPC     -> yes / no      (AP-side action)
  S3 notice GENERATED in GWPC-> yes / no      (GWPC-side artifact)

Non-outcomes:
  no declination notice without a decline; no notice generated when the quote was
  entered directly in Guidewire (see §4); no second notice on a duplicate submit
```

The core risk is `<` vs `<=`: a score of exactly 590 on an HO6 must **not** decline. The second
risk is that S2 and S3 are two independently-observable systems that can disagree — a notice can be
sent and never generated, or generated with no corresponding AP decline. Collapsing them into one
"expected result" column makes both defects untestable.

---

## 2. Boundary trios (B1-B8)

All rows: Source = Agency Portal, State = MA, LOB = Home, Transaction = New Business.

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

- **B2 / B5 are the whole point of the trio**: they pin `<` rather than `<=` at each threshold.
- **B7** is the cross-scope proof in one direction: HO6 also declines below HO4's threshold, so the
  two thresholds have not been swapped.
- **B8** is the mirror: a score that *would* decline under HO6's 590 must be issued as an HO4,
  proving per-policy-type routing rather than a single shared constant.

Per standard §3.3, B7 and B8 are the pair that turns red if someone swaps the two constants — B1-B6
alone would all still pass.

---

## 3. Scope negatives (N1-N6)

Each holds a score of **500** — deep on the declining side, so the *only* reason not to decline is
the scope — and varies one scoping dimension out of scope. All expect **not declined, no notice
sent, no notice generated**.

| # | Varied dimension | Value | Score | Expected |
|---|---|---|---|---|
| N1 | Source of Quote | Direct / Call Center (not Agency Portal) | 500 | Not declined, no notice |
| N2 | State | CT, NH, RI (any non-MA) | 500 | Not declined, no notice |
| N3 | Line of Business | Auto (not Home) | 500 | Not declined, no notice |
| N4 | Policy Type | HO3, HO5 (not HO4/HO6) | 500 | Not declined, no notice |
| N5 | Transaction | Endorsement, Renewal, Rewrite (not New Business) | 500 | Not declined, no notice |
| N6 | **Combination** | Agency Portal + MA + Home + HO6 + **Renewal** | 500 | Not declined, no notice |

N6 is the mandatory combination negative from standard §4.2: every dimension in scope except one.
It is the case that catches an implementation which ORs its scope predicates where the BRD ANDs
them — none of N1-N5 can see that defect on its own.

Each negative asserts the **absence** of both notice outcomes, not merely that the quote was
issued.

---

## 4. Guidewire-side quadrants (G1-G4)

From BRD §5.3 / §5.4. The asymmetry between the two entry points is the thing to pin: the rule
belongs to the Agency Portal, so a quote entered directly in Guidewire is blocked without ever
producing a declination notice.

| # | Entry point | Criteria met | Expected |
|---|---|---|---|
| G1 | GW direct | yes | Quote **blocked**, **no** declination notice generated |
| G2 | GW direct | no | Policy issued successfully, no notice |
| G3 | AP → GWPC | yes | Declined in AP, notice **sent and generated** in GWPC |
| G4 | AP → GWPC | no | Issued, no notice anywhere |

G1 vs. G3 is the assertion that cannot be written at all if S2/S3 are collapsed into one column:
both are "criteria met", both stop the quote, and they differ *only* in whether a notice artifact
exists downstream.

---

## 5. Open questions for the business analyst (E1-E10)

The BRD does not specify these. Per standard §6 they must not be resolved by guessing and encoding
current behavior. Each is phrased as a decision the BA must make; **each becomes an active test the
day it is answered**, and until then exists (if at all) as a skipped/expected-fail case naming the
question.

| # | Case | Decision the BA must make | Becomes |
|---|---|---|---|
| E1 | Credit score missing / no-hit / thin file | Decline, refer to underwriting, or proceed? | 1 case per chosen outcome |
| E2 | Score `0`, negative, or > 850 | Reject as invalid input, or evaluate against the threshold? | 3 out-of-domain cases (standard §3.1) |
| E3 | Score returned as a string, `null`, or non-numeric | Hard error, or treat as no-hit (and then E1 applies)? | 3 malformed-input cases (standard §3.2) |
| E4 | Credit bureau times out or returns 5xx | **Fail-open (issue)** or **fail-closed (decline)**? Must be explicit — it is a compliance decision, not an implementation detail | 2 cases (timeout, 5xx) |
| E5 | Two applicants (co-applicant) on one HO6 quote | Which score governs — primary, lowest, or average? | 1 case per rule, plus a boundary trio on the governing score |
| E6 | Score changes between quote and bind | Re-evaluate at bind, or honor the quote-time decision? | 2 cases (score crosses the threshold in each direction) |
| E7 | Duplicate submit of the same quote | Exactly one declination notice expected (idempotency — standard §4.4) | 1 idempotency case |
| E8 | MA-frozen / consumer-blocked credit file | The legal path differs from a low score; what is it? | 1 case |
| E9 | Test-data identity (§4: first name + last name + DOB + address) no longer maps to its intended band | Who owns the fixture bank, and how often is it re-verified? | 1 fixture-integrity smoke test per designated identity (see §6) |
| E10 | Notice generated in GWPC but the AP transaction rolls back | No orphaned declination notice — confirm and specify the compensating action | 1 case asserting absence in S3 after rollback |

E4 and E9 are the two that most often turn into production incidents: an unstated fail-open/closed
policy, and a fixture bank nobody owns.

---

## 6. BRD §4 — the score is triggered by test-data identity, not by an injectable input

**This is the structural constraint that governs the whole matrix.** Per §4 of the BRD, the credit
band is not a parameter the test can set. It is selected by the *identity* of the test applicant —
first name + last name + date of birth + address — which the bureau stub maps to a band. There is
no "set score = 589" knob.

Consequences:

1. **Every case in §2-§4 depends on a maintained identity→band fixture bank.** B2 ("HO6 at exactly
   590 is not declined") is really "the identity designated as the 590 identity still resolves to
   590". If that mapping drifts, B2 passes for the wrong reason, or fails with a diagnostic that
   points at the rule engine instead of at the fixture.
2. **"The fixture bank is correct and current" must itself be a tested precondition** — a smoke
   test asserting that each designated identity still returns its intended band, run before (or as
   part of) the matrix. Without it the suite rots silently: the tests stay green while testing
   nothing, which is worse than red.
3. The boundary identities (the 589/590/591 and 579/580/581 identities) are the highest-value
   entries in the bank and the ones most likely to drift, because they are the least "obviously
   wrong" if they shift by a few points.

**OtterWorks already has the machinery for exactly this pattern** and it should be the model for
any implementation:

- `testdata/harness/validate.py` — the criteria-driven validator for generated test data.
- `make testdata-validate NS=<ns>` — the command that runs it against a namespace
  (`Makefile:160`; `NS` is required).

The same shape applies inside this repo: any OtterWorks suite whose assertions depend on seeded
identities or seeded config values (see standard §8.13, T-112/T-113 — the seeded
`max_upload_size_mb` / `max_upload_size_bytes` values versus the enforced `MAX_UPLOAD_BYTES`
constant) needs the same "the fixtures still mean what the tests assume" precondition, or its
boundary trios are asserting against numbers nobody is checking.

---

## 7. Case count

| Group | Cases |
|---|---|
| Boundary trios (B1-B8) | 8 |
| Scope negatives (N1-N6) | 6 |
| Guidewire quadrants (G1-G4) | 4 |
| Open questions (E1-E10) | 10 |
| **Total** | **24** |

Plus, once E1-E10 are answered, one fixture-integrity smoke assertion per designated identity in
the bank (§6).
