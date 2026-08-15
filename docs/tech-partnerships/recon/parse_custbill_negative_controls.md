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

## Note on the shared workspace

During the session an extra bronze line (`line_no = 999`, `raw_line` `STALE TAIL RECORD`)
was briefly visible in `ow_tp.bronze.custbill_lines` for `ns=demo` — sibling-unit activity
on this shared demo workspace, not something this unit wrote. It was gone before the final
run; bronze was verified at 52 lines per file with `record_count = 52` immediately before
the parse and recon runs that produced the committed report. Had it still been present, the
`manifest record_count matches all landed lines` gate would have failed the run rather than
silently parsing an extra record — which is the intended behaviour.
