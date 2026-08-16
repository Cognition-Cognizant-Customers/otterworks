# Negative controls: `ow_tp_parse_custbill`

The recon report (`parse_custbill_fixedwidth.md`) is green for `ns=demo`: 100/100 rows
field-identical to the legacy `.psv`, zero quarantined, trailer counts reconciled. A clean
feed proves parity, but it proves the *quarantine* and the *trailer gate* only vacuously —
both would look identical if they were dead code. These two experiments show them biting.

Both were run in a throwaway namespace (`ns=reconneg`), built by copying the `ns=demo`
bronze rows for `CUSTBILL_DEMO_001.dat` and mutating the copy. `ns=demo` was never
modified, and every `ns='reconneg'` row was deleted afterwards from all five tables
(verified: 0 rows remaining in each). `ns=demo` was re-verified intact afterwards
(100 records, 0 rejects, 2 recon rows, both `recon_ok = true`) and the recon report was
regenerated against that state.

## A. An invalid record is quarantined, not emitted

Mutation: one detail line's 8-character `BILL-DATE` field overwritten with non-numeric
characters, record length left at 65.

```text
ns=reconneg: 49 parsed records, 1 quarantined
```

Quarantine row (`ow_tp.silver.custbill_rejects`):

```text
CUSTBILL_DEMO_001.dat | line 2 | invalid_bill_date
```

Exit code `0`, trailer gate still green because `50 = 49 + 1`. This is the deficiency the
legacy job had: it reformatted the same field textually and emitted the row into the
finance report with no validity check at all.

## B. A short file fails the run

Mutation: one detail line deleted (49 detail lines) while the `TRL` record still declares
50; the manifest `record_count` was adjusted to the lines actually present so the failure
lands on the trailer reconciliation gate rather than on the bronze manifest gate.

```text
FAILED: recon: trailer counts reconcile for every file failed -> CUSTBILL_DEMO_001.dat | 50 | 49 | 0
```

Exit code `1`. Recon row: `declared_trailer_count = 50`, `parsed_count = 49`,
`rejected_count = 0`, `recon_ok = false`.

This is ETL-0187 (2011): the legacy job computed the same comparison, logged it, and
exited `0` regardless.

## C. Lines from an unmanifested file fail the run

Mutation: one bronze line inserted for `CUSTBILL_ORPHAN_999.dat`, a file with no row in
`ow_tp.bronze.custbill_files`. This is what a half-landed file looks like mid-ingest: its
lines are already in bronze, its manifest row is not there yet.

```text
FAILED: gate: every bronze line belongs to a manifest file failed -> CUSTBILL_ORPHAN_999.dat | 1
```

Exit code `1`, before anything is written to silver. The other four bronze gates all drive
from the manifest and left-join the lines, so they can only catch a manifest row whose
lines are missing or miscounted; this one covers the opposite direction. The parse also
inner-joins the manifest, so even invoked on its own it cannot consume a line whose file
is not registered — but the gate failing the run, rather than the parse quietly skipping
the line, is the deliberate choice: silently dropping records is how the legacy job lost
them.

Run in throwaway namespace `ns=orphanneg`, built by copying one `ns=demo` manifest row and
its lines; `ns=demo` untouched, and all five tables verified back to 0 rows for
`ns='orphanneg'` afterwards.

## D. Unparseable legacy fields are reported as mismatches

Mutation: copies of the two legacy `.psv` files were written to a scratch directory under
`/tmp`; one `BILL-DATE` was changed to `20AB-01-01` and the corresponding amount was changed
to `not-a-number`. The real golden directory and committed report were not modified.

```text
CUSTBILL_DEMO_001 line 2 bill_date: legacy unparseable legacy bill_date '20AB-01-01' != converted datetime.date(2025, 3, 23)
CUSTBILL_DEMO_001 line 2 amount: legacy unparseable legacy amount 'not-a-number' != converted Decimal('4393.35')
```

Exit code `1`; the report was written to `/tmp/recon-golden-corrupt-report.md` rather than
raising a traceback. Check 1 names both raw legacy values as unparseable and compares them
directly with the converted typed values, so corrupt legacy output is visible as a parity
mismatch rather than being coerced, skipped, or mistaken for a conversion failure.

## E. Malformed legacy lines fail the report, not the process

Mutation: copies of the two legacy `.psv` files were written to a scratch directory under
`/tmp`; one data line was changed from six pipe-separated fields to five. The real golden
directory and committed report were not modified.

```text
CUSTBILL_DEMO_001.psv line 1 (source line 2): malformed legacy line: expected 6 fields, got 5: 'C000699637|INITECH SA|2025-03-23|4393.35|USD'
```

Exit code `1`; the report was written to `/tmp/recon-golden-malformed-report.md` rather than
raising a traceback. Check 0 records the file, line number, expected field count, actual
field count, and raw content; row parity also fails because the malformed legacy record is
not silently padded or treated as a valid six-field row.

## F. A trailer mismatch does not replace previously published rows

Mutation: a valid `ns=stagingneg` copy of `CUSTBILL_DEMO_001.dat` was published first. One
detail line was then deleted from bronze and the manifest `record_count` was adjusted from
52 to 51 so the bronze gates remained green while the trailer still declared 50.

Before the failed run:

```text
published records: 50
published recon: CUSTBILL_DEMO_001.dat | declared 50 | parsed 50 | rejected 0 | recon_ok true
```

The failed run stopped at the staged trailer gate:

```text
FAILED: staged recon: trailer counts reconcile for every file failed -> CUSTBILL_DEMO_001.dat | 50 | 49 | 0
```

Exit code `1`. After the failed run, the published rows were unchanged:

```text
published records: 50
published recon: CUSTBILL_DEMO_001.dat | declared 50 | parsed 50 | rejected 0 | recon_ok true
```

The eight bronze, published-silver, and staging tables were then verified back to `0`
rows for `ns='stagingneg'`. This proves failed staged output is not queryable in published
silver and the previous good namespace remains intact.

## G. An unreadable trailer count fails both the pipeline gate and the report

### Pipeline-side staged gate

Mutation: in throwaway namespace `ns=trlneg2`, the `TRL` line for
`CUSTBILL_DEMO_001.dat` was changed to the non-numeric value `TRLNOTNUM`. The staged
reconciliation row contained `declared_trailer_count = NULL` and `recon_ok = NULL`.
The staged gate predicate `recon_ok IS NOT TRUE` caught that row:

```text
FAILED: staged recon: trailer counts reconcile for every file failed -> CUSTBILL_DEMO_001.dat | None | 50 | 0
```

The pipeline exited `1` before any publish statements ran. Published rows were preserved:
the namespace had 50 published records and `declared 50 | parsed 50 | rejected 0 |
recon_ok true` before the mutation, and the same values afterward. The eight bronze,
published-silver, and staging tables were verified back to `0` rows for `ns='trlneg2'`.

### Recon-side report

Mutation: in throwaway namespace `ns=trlneg`, the `TRL` line for
`CUSTBILL_DEMO_001.dat` was changed to the non-numeric value `TRLNOTNUM`, producing a
NULL `declared_trailer_count` in the reconciliation row.

The recon report was written to `/tmp/trlneg-null-report.md` and exited `1`. Check 3
reported the corrupt trailer as a failure:

```text
CUSTBILL_DEMO_001.dat: unreadable TRL record: declared trailer count is NULL (parsed_count='50', rejected_count='0', recon_ok='false')
```

There was no traceback. All eight bronze, published-silver, and staging tables were
verified back to `0` rows for `ns='trlneg'` afterwards.

## H. An empty golden directory is blocked, not reported as a match

Mutation: recon was pointed at the empty scratch directory `/tmp/empty-golden`, which
contains no files matching `CUSTBILL_DEMO_*.psv`.

The report was written to `/tmp/empty-golden-report.md` and exited `1`. The
golden-dependent checks were all blocked, including trailer reconciliation (which
still validated the two rows it found but skipped the baseline row-count
comparison):

```text
| 1. Row-level parity: every field of every row, keyed on (file, line_no) | **BLOCKED** |
| 2. Per-file subtotals per record type and currency, exact to the cent | **BLOCKED** |
| 3. Trailer reconciliation: declared_trailer_count = parsed + rejected, recon_ok | **BLOCKED** |
| 4. Quarantine justified: nothing the legacy output contains is rejected | **BLOCKED** |
Blocked: `golden output` -> no rows loaded from /tmp/empty-golden using CUSTBILL_DEMO_*.psv
```

The report result was `red` because the baseline check could not find its two
contract files; the empty baseline is not an evaluable success.
The committed report and `ns=demo` were restored and left untouched.

## I. An untypeable converted amount is reported without a traceback

The shared published table rejected an attempt to set `amount = NULL` in throwaway
namespace `ns=convnullamt` with the same NOT NULL constraint used by the typed
silver table, so the converted reader/report path was exercised with the same
nullable-row shape in an isolated harness. It wrote
`/tmp/converted-amount-report.md`, exited `1`, and produced no traceback.

```text
CUSTBILL_DEMO_001 line 2 amount: legacy Decimal('4393.35') != converted unparseable converted amount None
before re-run: unparseable converted amount at CUSTBILL_DEMO_001 line 2: unparseable converted amount None
after re-run: unparseable converted amount at CUSTBILL_DEMO_001 line 2: unparseable converted amount None
```

The direct mutation attempt produced:

```text
[DELTA_NOT_NULL_CONSTRAINT_VIOLATED] NOT NULL constraint violated for column: amount.
```

The namespace was cleaned back to zero rows in all eight tables. The report also
showed check 5 as `FAIL`, with totals marked unavailable rather than raising:

```text
before re-run: 100 rows, total unavailable
after re-run: 100 rows, total unavailable
row count or amount total changed across a re-run
```

## J. Standalone parse cannot erase a published namespace when bronze is empty

Mutation: namespace `ns=parseguard` was populated and parsed successfully, then
its bronze manifest and lines were deleted. Before the standalone parse attempt,
both published files had 50 records and healthy reconciliation rows:

```text
before_records [['CUSTBILL_DEMO_001.dat', '50'], ['CUSTBILL_DEMO_002.dat', '50']]
before_recon [['CUSTBILL_DEMO_001.dat', '50', '50', '0', 'true'], ['CUSTBILL_DEMO_002.dat', '50', '50', '0', 'true']]
```

The parse task now runs the read-only bronze manifest gate itself before staging.
It failed before any staging or publication statement ran:

```text
bronze manifest gate
FAILED: gate: bronze manifest is present failed -> no files in manifest
parse_exit=1
```

The published namespace remained intact:

```text
after_records [['CUSTBILL_DEMO_001.dat', '50'], ['CUSTBILL_DEMO_002.dat', '50']]
after_recon [['CUSTBILL_DEMO_001.dat', '50', '50', '0', 'true'], ['CUSTBILL_DEMO_002.dat', '50', '50', '0', 'true']]
```

The staged recon gate also contains a second defense for any future path that
reaches it with empty staging: `staged output is not empty when published rows
exist`. Its failure text is `empty staged result would have erased N published
rows`. The other empty-namespace assertions are intentionally harmless:
the bronze manifest gate rejects an empty manifest first, while duplicate checks
and missing-recon checks operate on the actual rows/files and cannot erase data.

## K. An unknown manifest record count fails the handshake

The `manifest record_count matches all landed lines` predicate now treats a
NULL declaration as an error (`record_count IS NULL OR record_count <> landed`).
The shared workspace's existing bronze table has an enforced NOT NULL constraint,
so the direct mutation attempt in throwaway namespace `ns=kneg` was rejected by
the warehouse before the gate could run:

```text
[DELTA_NOT_NULL_CONSTRAINT_VIOLATED] NOT NULL constraint violated for column: record_count.
```

To exercise the nullable shape without weakening or altering the ingest-owned
table, the same gate query was run against an isolated manifest projection with
`CUSTBILL_DEMO_001.dat`'s `record_count` replaced by SQL NULL. It returned the
file, proving the NULL branch is not a vacuous pass:

```text
isolated_null_count_gate [['CUSTBILL_DEMO_001.dat', None, '52']]
```

The namespace had 100 published rows before cleanup and all eight bronze,
published-silver, and staging tables were returned to zero rows. The attempted
mutation could not reach the parse task; therefore no claim is made that the
live constrained table produced a full end-to-end NULL-count run.

## Note on the shared workspace

During the session an extra bronze line (`line_no = 999`, `raw_line` `STALE TAIL RECORD`)
was briefly visible in `ow_tp.bronze.custbill_lines` for `ns=demo` — sibling-unit activity
on this shared demo workspace, not something this unit wrote. It was gone before the final
run; bronze was verified at 52 lines per file with `record_count = 52` immediately before
the parse and recon runs that produced the committed report. Had it still been present, the
`manifest record_count matches all landed lines` gate would have failed the run rather than
silently parsing an extra record — which is the intended behaviour.
