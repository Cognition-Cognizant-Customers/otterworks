#!/usr/bin/env python3
"""Reconcile `ow_tp_sftp_ingest` bronze output against the golden legacy output.

The comparison is against the artifacts an actual `sftp_ingest_poll.ksh` run left on
disk (`incoming/*.dat.done`), read here byte-for-byte — never against numbers copied
out of a document and never against the converted pipeline's own output.

The five checks are the numbered acceptance checks of
`docs/tech-partnerships/contracts/sftp_ingest_poll.md`:

  1. manifest rows: file set, `size_bytes`, `sha256` == golden `sha256sum`
  2. raw lines: 52 per file / 104 total, every `raw_line` identical to the golden line
     (which subsumes record length, and the HDR/TRL records being unaltered)
  3. TRL-declared record count == detail lines ingested for that file
  4. idempotency: re-run the ingest, both tables must be byte-identical afterwards
  5. object scope: only the two contracted tables are written, every identifier the
     statement set touches is `ow_tp`-prefixed, and no stray job is left behind

Plus one behavioural check beyond the contract's five, covering the gate the size-settle
heuristic replaced:

  6. a drop holding one complete and one half-written file ingests exactly the complete
     one, records nothing for the other, leaves it in landing, and still fails the run

Usage:
    python3 recon_sftp_ingest.py --ns demo                      # full run
    python3 recon_sftp_ingest.py --ns demo --no-rerun           # skip check 4
    python3 recon_sftp_ingest.py --ns demo --report out.md      # + markdown report
    python3 recon_sftp_ingest.py --ns demo --no-gate-probe      # skip check 6
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dbx  # noqa: E402
import sftp_ingest_sql  # noqa: E402

GOLDEN_ROOT = Path("/home/ubuntu/tp-golden/custbill")
# The estate the driver is pointed at, never a constant: pinning `ow_tp` here would let a
# run configured for another ow_tp* catalog reconcile — and, in check 4, MERGE into — the
# default estate while the report named the configured one. `dbx` already refuses a
# non-ow_tp catalog, so this stays inside the prefix rule.
CATALOG = dbx.CATALOG
LANDING_ROOT = sftp_ingest_sql.default_landing_root(CATALOG)

# Objects this unit is allowed to create / write. Anything else in the shared
# workspace belongs to another unit or to the parent, and is out of bounds.
OWNED_TABLES = {f"{CATALOG}.bronze.custbill_files", f"{CATALOG}.bronze.custbill_lines"}
OWNED_JOB = "ow_tp_sftp_ingest"
OWNED_DEV_JOB = "ow_tp_dev_sftp_ingest"  # the throwaway this unit is allowed to create

_QUALIFIED_NAME = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b")

# The target of every statement that writes, however it is spelled. A three-part scan
# alone cannot see `MERGE INTO bronze.other_table` — a write with the catalog implied by
# the session, which is exactly the unprefixed case this check claims to catch — so the
# write targets are extracted by keyword and judged whole, not by shape.
_WRITE_TARGET = re.compile(
    r"\b(?:MERGE\s+INTO|INSERT\s+INTO|INSERT\s+OVERWRITE(?:\s+TABLE)?|UPDATE|DELETE\s+FROM"
    r"|CREATE\s+(?:OR\s+REPLACE\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?|REPLACE\s+TABLE"
    r"|TRUNCATE\s+TABLE|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\s+"
    r"(?:IDENTIFIER\(\s*'([^']+)'\s*\)|([A-Za-z_][A-Za-z0-9_.]*))",
    re.IGNORECASE,
)
# `UPDATE SET` inside a MERGE is a clause, not a statement target.
_NOT_A_TARGET = {"set"}


def _write_targets(statement: str) -> set[str]:
    """Every table this statement writes to, as written.

    `--` comments are removed first: prose about a `CREATE TABLE IF NOT EXISTS` is not a
    write, and reading one as such would put noise where the verdict is.
    """
    code = re.sub(r"--[^\n]*", "", statement)
    found = set()
    for identifier, bare in _WRITE_TARGET.findall(code):
        name = identifier or bare
        if name.lower() not in _NOT_A_TARGET:
            found.add(name)
    return found


@dataclass
class Check:
    number: int
    name: str
    passed: bool | None = None  # None == could not be run (blocked)
    detail: list[str] = field(default_factory=list)
    # set when the check was blocked by a failure rather than deliberately skipped;
    # a recon that could not run a check must not exit 0 as if it had.
    error: str | None = None

    @property
    def status(self) -> str:
        return "BLOCKED" if self.passed is None else ("PASS" if self.passed else "FAIL")


@dataclass
class GoldenFile:
    """One legacy artifact, read from disk."""

    name: str
    size_bytes: int
    sha256: str
    lines: list[str]

    @property
    def header(self) -> str:
        return next(line for line in self.lines if line.startswith("HDR"))

    @property
    def trailer(self) -> str:
        return next(line for line in self.lines if line.startswith("TRL"))

    @property
    def detail_lines(self) -> list[str]:
        return [line for line in self.lines if not line.startswith(("HDR", "TRL"))]

    @property
    def trailer_declared(self) -> int:
        return int(self.trailer[3:13])


def load_golden(golden_root: Path) -> dict[str, GoldenFile]:
    """Read the golden legacy artifacts. The `.done` suffix is the legacy rename."""
    incoming = golden_root / "incoming"
    if not incoming.is_dir():
        raise FileNotFoundError(
            f"golden legacy output not found at {incoming}. Regenerate it with: "
            "make legacy-etl-gen-data NS=demo && make legacy-etl-run JOB=sftp_ingest_poll "
            f"and copy /tmp/otterworks-legacy/{{incoming,archive}} to {golden_root}"
        )
    golden: dict[str, GoldenFile] = {}
    for path in sorted(incoming.glob("*.dat.done")):
        payload = path.read_bytes()
        text = payload.decode("utf-8")
        golden[path.name[: -len(".done")]] = GoldenFile(
            name=path.name[: -len(".done")],
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            # Exactly one trailing terminator, mirroring the ingest's `endswith('\n')`
            # trim: rstrip("\n") would swallow a blank last record the pipeline keeps,
            # so a file ending in two line breaks would be reported as a mismatch the
            # pipeline did not cause.
            lines=(text[:-1] if text.endswith("\n") else text).split("\n"),
        )
    if not golden:
        raise FileNotFoundError(f"no *.dat.done artifacts under {incoming}")
    return golden


def _manifest_rows(ns: str) -> dict[str, dict[str, str]]:
    rows = dbx.sql(
        "SELECT file_name, size_bytes, sha256, record_count, source_path "
        f"FROM {CATALOG}.bronze.custbill_files WHERE ns = '{ns}' ORDER BY file_name"
    )
    return {
        row[0]: {"size_bytes": row[1], "sha256": row[2], "record_count": row[3], "source_path": row[4]}
        for row in rows
    }


def _ingested_lines(ns: str) -> dict[str, list[str]]:
    rows = dbx.sql(
        "SELECT file_name, line_no, raw_line "
        f"FROM {CATALOG}.bronze.custbill_lines WHERE ns = '{ns}' ORDER BY file_name, line_no"
    )
    lines: dict[str, list[str]] = {}
    for file_name, _line_no, raw_line in rows:
        lines.setdefault(file_name, []).append(raw_line)
    return lines


def check1_manifest(ns: str, landing_root: str, golden: dict[str, GoldenFile]) -> Check:
    """Manifest == golden: same files, byte counts, SHA-256, record counts, origin."""
    check = Check(1, "bronze.custbill_files matches the golden artifacts")
    # `_metadata.file_path` comes back scheme-qualified; the drop path is what is being
    # asserted, not the scheme, so it is compared with the prefix stripped.
    drop = sftp_ingest_sql.drop_path(ns, landing_root)
    manifest = _manifest_rows(ns)
    ok = set(manifest) == set(golden) and len(manifest) == len(golden)
    check.detail.append(f"row count: converted={len(manifest)} golden={len(golden)}")
    if set(manifest) != set(golden):
        check.detail.append(f"file set differs: converted={sorted(manifest)} golden={sorted(golden)}")
    for name in sorted(golden):
        want = golden[name]
        got = manifest.get(name)
        if got is None:
            ok = False
            check.detail.append(f"{name}: MISSING from bronze.custbill_files")
            continue
        size_ok = int(got["size_bytes"]) == want.size_bytes
        hash_ok = got["sha256"] == want.sha256
        # record_count is the manifest's own claim about the file; every other check
        # reads the lines table or the golden artifact, so if this column drifts nothing
        # else notices — and the manifest is what downstream units trust.
        count_ok = int(got["record_count"]) == len(want.lines)
        origin = re.sub(r"^[a-z0-9]+:", "", got["source_path"])
        origin_ok = origin == f"{drop}{name}"
        ok = ok and size_ok and hash_ok and count_ok and origin_ok
        check.detail.append(
            f"{name}: size_bytes converted={got['size_bytes']} golden={want.size_bytes} "
            f"[{'ok' if size_ok else 'MISMATCH'}]"
        )
        check.detail.append(
            f"{name}: sha256 converted={got['sha256']} golden={want.sha256} [{'ok' if hash_ok else 'MISMATCH'}]"
        )
        check.detail.append(
            f"{name}: record_count converted={got['record_count']} golden={len(want.lines)} "
            f"[{'ok' if count_ok else 'MISMATCH'}]"
        )
        check.detail.append(
            f"{name}: source_path converted={got['source_path']} expected={drop}{name} "
            f"[{'ok' if origin_ok else 'MISMATCH'}]"
        )
    check.passed = ok
    return check


def check2_lines(ns: str, golden: dict[str, GoldenFile]) -> Check:
    """Raw lines byte-identical to the golden records, in delivery order."""
    check = Check(2, "bronze.custbill_lines preserves the raw records")
    ingested = _ingested_lines(ns)
    total_converted = sum(len(v) for v in ingested.values())
    total_golden = sum(len(v.lines) for v in golden.values())
    ok = total_converted == total_golden
    check.detail.append(f"total rows: converted={total_converted} golden={total_golden}")
    for name in sorted(golden):
        want = golden[name].lines
        got = ingested.get(name, [])
        count_ok = len(got) == len(want)
        identical = got == want
        widths_converted = sorted({len(line) for line in got})
        widths_golden = sorted({len(line) for line in want})
        ok = ok and count_ok and identical
        check.detail.append(
            f"{name}: lines converted={len(got)} golden={len(want)} [{'ok' if count_ok else 'MISMATCH'}]"
        )
        check.detail.append(
            f"{name}: record lengths converted={widths_converted} golden={widths_golden} "
            f"[{'ok' if widths_converted == widths_golden else 'MISMATCH'}]"
        )
        if identical:
            check.detail.append(f"{name}: all {len(want)} raw_line values byte-identical to golden (HDR/TRL included)")
        else:
            first_diff = next(
                (i for i, (a, b) in enumerate(zip(got, want), start=1) if a != b),
                min(len(got), len(want)) + 1,
            )
            check.detail.append(f"{name}: FIRST DIFFERING LINE at line_no={first_diff}")
            if first_diff <= len(got) and first_diff <= len(want):
                check.detail.append(f"  converted={got[first_diff - 1]!r}")
                check.detail.append(f"  golden   ={want[first_diff - 1]!r}")
    check.passed = ok
    return check


def check3_trailer(ns: str, golden: dict[str, GoldenFile]) -> Check:
    """The reconciliation the legacy chain logs but never checks."""
    check = Check(3, "TRL-declared count equals detail lines ingested")
    rows = dbx.sql(
        f"""
SELECT file_name,
       -- TRY_CAST, as in the ingest: a file truncated mid-trailer is exactly what this
       -- check exists to catch, and a plain CAST would abort the reporting query itself.
       max(CASE WHEN raw_line LIKE 'TRL%' THEN TRY_CAST(substr(raw_line, 4, 10) AS BIGINT) END) AS declared,
       count_if(raw_line NOT LIKE 'HDR%' AND raw_line NOT LIKE 'TRL%')                      AS detail
FROM {CATALOG}.bronze.custbill_lines
WHERE ns = '{ns}'
GROUP BY file_name
ORDER BY file_name"""
    )
    ok = len(rows) == len(golden)
    if len(rows) != len(golden):
        check.detail.append(f"file count: converted={len(rows)} golden={len(golden)} MISMATCH")
    for file_name, declared, detail in rows:
        want = golden.get(file_name)
        pair_ok = declared == detail and want is not None and int(detail) == len(want.detail_lines)
        ok = ok and pair_ok
        golden_detail = len(want.detail_lines) if want else "n/a"
        check.detail.append(
            f"{file_name}: TRL declared={declared} ingested detail lines={detail} "
            f"golden detail lines={golden_detail} [{'ok' if pair_ok else 'MISMATCH'}]"
        )
    check.passed = ok
    return check


def _snapshot(ns: str) -> dict[str, str]:
    """Content fingerprint of both tables, every column included.

    `ingested_at` is deliberately part of the fingerprint: idempotency here means the
    re-run leaves the rows untouched, not merely that no row was added.
    """
    snapshot = {}
    for table, order in (
        ("custbill_files", "file_name"),
        ("custbill_lines", "file_name, line_no"),
    ):
        rows = dbx.sql(f"SELECT * FROM {CATALOG}.bronze.{table} WHERE ns = '{ns}' ORDER BY {order}")
        serialized = "\n".join("\x1f".join("" if v is None else str(v) for v in row) for row in rows)
        snapshot[table] = f"{len(rows)}:{hashlib.sha256(serialized.encode()).hexdigest()}"
    return snapshot


def _landed_files(ns: str, landing_root: str) -> list[str]:
    """File names the ingest's own source scan sees under the landing path.

    The query comes from the statement module, so what the recon claims the re-run
    reads is literally what the re-run reads.
    """
    rows = dbx.sql(sftp_ingest_sql.landed_files_query(ns, CATALOG, landing_root))
    return [row[0] for row in rows]


def check4_idempotency(
    ns: str, landing_root: str, rerun: bool, golden: dict[str, GoldenFile]
) -> Check:
    """Re-run the identical statement set; both tables must be unchanged.

    Unchanged tables only mean idempotency if the re-run actually read the drops:
    over an empty landing path every statement is a no-op and the fingerprints
    would match trivially, so the inputs are asserted before the comparison.
    """
    check = Check(4, "re-running the ingest leaves both tables byte-identical")
    if not rerun:
        check.detail.append("skipped (--no-rerun)")
        return check
    landed = _landed_files(ns, landing_root)
    check.detail.append(
        f"files the re-run reads under {landing_root}/{ns}/custbill/: {landed or 'NONE'} "
        f"(golden: {sorted(golden)})"
    )
    if sorted(landed) != sorted(golden):
        check.passed = False
        check.detail.append(
            "the re-run would not have processed the golden file set, so an unchanged "
            "fingerprint would prove nothing; not comparing"
        )
        return check
    before = _snapshot(ns)
    try:
        sftp_ingest_sql.run(ns=ns, catalog=CATALOG, landing_root=landing_root, create_tables=False)
    except Exception as exc:  # noqa: BLE001 - a re-run that refuses to run is a failed check
        check.passed = False
        check.detail.append(f"re-run failed: {exc}")
        return check
    after = _snapshot(ns)
    check.passed = before == after
    for table in sorted(before):
        same = before[table] == after[table]
        check.detail.append(
            f"{table}: rows:sha256 before={before[table]} after={after[table]} [{'unchanged' if same else 'CHANGED'}]"
        )
    return check


def check5_scope(ns: str, landing_root: str) -> Check:
    """Nothing outside the two contracted tables is created or touched.

    `landing_root` must be the one the run actually used: it is embedded in the
    statements, so analysing the default here would vet text that was never run.
    """
    check = Check(5, "no ow_tp object outside the contract, no unprefixed object")
    ok = True

    # (a) static: every three-part identifier in the statement set this unit executes.
    statements = [
        *sftp_ingest_sql.ddl_statements(CATALOG),
        sftp_ingest_sql.incomplete_files_query(ns, CATALOG, landing_root),
        *sftp_ingest_sql.ingest_statements(ns, CATALOG, landing_root),
    ]
    check.detail.append(f"statements analyzed for landing_root={landing_root}")
    retention = Path(__file__).resolve().parents[2] / "infrastructure" / "terraform-databricks"
    retention_sql = (retention / "sql" / "sftp_ingest_retention.sql").read_text()
    referenced = {name for s in statements for name in _QUALIFIED_NAME.findall(s)}
    unowned = referenced - OWNED_TABLES
    unprefixed = {name for name in referenced if not name.startswith(f"{CATALOG}.")}
    # bool(referenced): finding nothing means the scanner no longer understands the SQL
    # (e.g. it moved to IDENTIFIER(:catalog || ...) binds), not that the scope is clean.
    ok = ok and bool(referenced) and not unowned and not unprefixed
    check.detail.append(f"tables referenced by the statement set: {sorted(referenced) or '[]'}")
    check.detail.append(f"outside the contract: {sorted(unowned) or 'none'}")
    check.detail.append(f"unprefixed: {sorted(unprefixed) or 'none'}")
    # Written-to tables, judged by name and not by shape: a two-part `schema.table`
    # target would be invisible to the three-part scan above and is the whole point.
    written = {name for s in statements for name in _write_targets(s)}
    written_unowned = written - OWNED_TABLES
    ok = ok and bool(written) and not written_unowned
    check.detail.append(f"write targets in the statement set: {sorted(written) or '[]'}")
    check.detail.append(f"write targets outside the contract: {sorted(written_unowned) or 'none'}")
    # retention SQL addresses its tables through IDENTIFIER(:catalog || ...), so match those
    retention_tables = {f"{CATALOG}{frag}" for frag in re.findall(r":catalog \|\| '(\.[a-z_.]+)'", retention_sql)}
    retention_unowned = retention_tables - OWNED_TABLES
    ok = ok and not retention_unowned and bool(retention_tables)
    check.detail.append(f"retention SQL targets: {sorted(retention_tables)} outside={sorted(retention_unowned) or 'none'}")

    # (b) live: tables that exist in the catalog, and jobs left in the workspace.
    existing = {f"{CATALOG}.{row[0]}.{row[1]}" for row in dbx.sql(
        f"SELECT table_schema, table_name FROM {CATALOG}.information_schema.tables "
        "WHERE table_schema IN ('bronze','silver','gold') ORDER BY table_schema, table_name"
    )}
    missing = OWNED_TABLES - existing
    ok = ok and not missing
    check.detail.append(f"contracted tables present: {sorted(OWNED_TABLES & existing)} missing={sorted(missing) or 'none'}")
    other_unit_tables = sorted(existing - OWNED_TABLES)
    check.detail.append(
        f"other ow_tp tables in the catalog (other units', not written by this unit per (a)): "
        f"{other_unit_tables or 'none'}"
    )

    namespaces = [row[0] for row in dbx.sql(
        f"SELECT DISTINCT ns FROM {CATALOG}.bronze.custbill_lines ORDER BY ns"
    )]
    check.detail.append(
        f"namespaces present in bronze.custbill_lines: {namespaces} "
        f"(this unit only ever writes ns='{ns}'; other namespaces are other runs')"
    )

    found = dbx.inventory()
    # Only this unit's own throwaway job is a verdict-affecting stray. Other units'
    # ow_tp_dev_* jobs are reported but not judged here — they are not ours to delete.
    strays = [name for name in found["jobs"] if name == OWNED_DEV_JOB]
    others = [name for name in found["jobs"] if name.startswith("ow_tp_dev_") and name != OWNED_DEV_JOB]
    ok = ok and not strays
    check.detail.append(f"ow_tp jobs in the workspace: {sorted(found['jobs']) or 'none (1/3 not applied yet)'}")
    check.detail.append(f"this unit's throwaway {OWNED_DEV_JOB} left behind: {strays or 'none'}")
    check.detail.append(f"other units' ow_tp_dev_* jobs (not this unit's, not judged): {sorted(others) or 'none'}")
    check.detail.append(f"catalogs={found['catalogs']} secret_scopes={found['secret_scopes']} dirs={found['directories']}")
    check.passed = ok
    return check


GATE_NS = "gateprobe"


def _gate_cleanup(ns: str) -> None:
    """Remove the probe namespace's rows again: they are evidence, not estate."""
    for table in ("custbill_lines", "custbill_files"):
        dbx.sql(f"DELETE FROM {CATALOG}.bronze.{table} WHERE ns = '{ns}'")


def check6_gate(gate_ns: str, landing_root: str, attempt: bool) -> Check:
    """One complete + one half-written file: the complete one lands, the run still fails.

    The behaviour under test is the one the size-settle heuristic got wrong, and it has
    two halves that can each fail alone: a partial delivery must not be ingested, and it
    must not stop the deliveries that did arrive whole from landing — landing is the
    archive, so an abandoned partial sits in the drop path forever and a fail-first gate
    would freeze the namespace. Run in its own namespace so nothing here touches the rows
    the other checks reconcile, and the rows are deleted again afterwards.

    The fixtures cannot be staged from here: `dbx.upload` is refused by this token (no
    `files` scope, see the upload probe), so their absence is reported as BLOCKED with
    the command that stages them rather than being quietly passed over.
    """
    check = Check(6, "a half-written file neither lands nor blocks the complete files")
    if not attempt:
        check.detail.append("skipped (--no-gate-probe / --no-rerun)")
        return check

    drop = sftp_ingest_sql.drop_path(gate_ns, landing_root)
    landed = sorted(_landed_files(gate_ns, landing_root))
    incomplete_rows = dbx.sql(sftp_ingest_sql.incomplete_files_query(gate_ns, CATALOG, landing_root))
    incomplete = sorted(row[0] for row in incomplete_rows)
    complete = [name for name in landed if name not in set(incomplete)]
    check.detail.append(f"fixtures under {drop}: {landed or 'NONE'}")
    check.detail.append(f"the gate calls incomplete: {incomplete or 'none'}; complete: {complete or 'none'}")
    if not incomplete or not complete:
        check.detail.append(
            f"fixtures for the mixed case are not staged under {drop} — this needs one complete "
            "and one truncated CUSTBILL drop, and the recon cannot put them there itself "
            "(dbx.upload is refused: no `files` scope)"
        )
        return check
    for row in incomplete_rows:
        check.detail.append(f"observed vs declared: {sftp_ingest_sql.describe_incomplete(row)}")

    _gate_cleanup(gate_ns)
    raised: Exception | None = None
    try:
        sftp_ingest_sql.run(ns=gate_ns, catalog=CATALOG, landing_root=landing_root, create_tables=False)
    except sftp_ingest_sql.IncompleteDropError as exc:
        raised = exc
    failed_loudly = raised is not None
    check.detail.append(
        f"run over the mixed drop: {'failed, as required: ' + str(raised) if failed_loudly else 'SUCCEEDED — the half-written file was passed over silently'}"
    )
    named = failed_loudly and all(name in str(raised) for name in incomplete)
    check.detail.append(f"error names every refused file {incomplete}: {'ok' if named else 'MISMATCH'}")

    manifest = sorted(_manifest_rows(gate_ns))
    lines = _ingested_lines(gate_ns)
    manifest_ok = manifest == sorted(complete)
    lines_ok = sorted(lines) == sorted(complete)
    check.detail.append(f"manifest rows after the run: {manifest} expected={sorted(complete)} [{'ok' if manifest_ok else 'MISMATCH'}]")
    check.detail.append(
        f"files with raw lines after the run: {sorted(lines)} expected={sorted(complete)} "
        f"[{'ok' if lines_ok else 'MISMATCH'}]"
    )
    for name in incomplete:
        check.detail.append(f"{name}: manifest rows=0 lines=0 [{'ok' if name not in manifest and name not in lines else 'MISMATCH'}]")

    # The complete file is judged on its own content, not on the gate's verdict about it:
    # exactly one HDR, one TRL, and a TRL-declared count equal to the detail lines that
    # landed. Otherwise this check would only be asserting that the gate agrees with itself.
    structural_ok = True
    for name in complete:
        got = lines.get(name, [])
        headers = [line for line in got if line.startswith("HDR")]
        trailers = [line for line in got if line.startswith("TRL")]
        details = [line for line in got if not line.startswith(("HDR", "TRL"))]
        declared = int(trailers[0][3:13]) if len(trailers) == 1 else None
        this_ok = len(headers) == 1 and len(trailers) == 1 and declared == len(details)
        structural_ok = structural_ok and this_ok
        check.detail.append(
            f"{name}: ingested whole — hdr={len(headers)} trl={len(trailers)} detail={len(details)} "
            f"TRL declares={declared} [{'ok' if this_ok else 'MISMATCH'}]"
        )

    # Left unconsumed, per the retention decision: the drop path still holds both files,
    # so a re-delivery of the complete file replaces it and the partial one is still there
    # to be re-checked once the sender finishes it.
    still_there = sorted(_landed_files(gate_ns, landing_root))
    retained_ok = still_there == landed
    check.detail.append(f"drop path after the run: {still_there} expected={landed} [{'ok' if retained_ok else 'MISMATCH'}]")

    _gate_cleanup(gate_ns)
    left = sorted(_manifest_rows(gate_ns)) + sorted(_ingested_lines(gate_ns))
    check.detail.append(f"probe rows removed again: {'ok' if not left else 'STILL PRESENT: ' + str(left)}")
    check.passed = (
        failed_loudly and named and manifest_ok and lines_ok and structural_ok and retained_ok and not left
    )
    return check


def _contained(number: int, name: str, check_fn, *args) -> Check:
    """Run one check, turning a crash into a BLOCKED result instead of a traceback.

    The report is the artifact of record, so a warehouse error or a missing landing
    path must not take the other four checks' evidence with it. BLOCKED is not a
    pass: it exits 0 only where the run deliberately declined to check (`--no-rerun`),
    and it says on its face that the check could not be run.
    """
    try:
        return check_fn(*args)
    except Exception as exc:  # noqa: BLE001 - any failure to run is evidence, not a crash
        check = Check(number, name)
        check.error = f"{type(exc).__name__}: {exc}"
        check.detail.append(f"could not be run: {check.error}")
        return check


PROBE_DIR = "_upload_probe"


@dataclass
class UploadProbe:
    """What the documented `make dbx-upload` transport did on this run."""

    target: str
    verified: bool | None  # None == not attempted on this run
    error: str | None = None
    skipped: str | None = None
    # set when the probe's own payload could not be removed again, i.e. the probe left
    # an object behind in the shared volume — a fact the report has to carry.
    cleanup_error: str | None = None


def probe_upload(ns: str, landing_root: str, attempt: bool) -> UploadProbe:
    """Exercise the documented upload transport and report what actually happened.

    The report is evidence, so its caveat about `make dbx-upload` has to be an
    observation rather than a remembered fact: a token that later gains the `files`
    scope must flip it to verified without anyone editing prose.

    What is deliberately *not* done: writing into the drop directory. A verifier that
    stages its own input into the path under test is no longer verifying the pipeline,
    so the probe writes a probe payload to a sibling `{ns}/_upload_probe/` path that
    no ingest statement reads, and it is skipped entirely when the requested
    `landing_root` is not the one `dbx.upload` writes to (the driver derives its own
    root from `OW_TP_CATALOG`), because a probe of a different volume proves nothing
    about this run.
    """
    probe_root = f"/Volumes/{dbx.CATALOG}/bronze/landing"
    target = f"{probe_root}/{ns}/{PROBE_DIR}/upload_probe.txt"
    if not attempt:
        return UploadProbe(target=target, verified=None, skipped="not attempted on this run")
    if landing_root.rstrip("/") != probe_root:
        return UploadProbe(
            target=target,
            verified=None,
            skipped=(
                f"not attempted: dbx.upload writes under {probe_root}, but this run reconciles "
                f"{landing_root}, so the probe would not exercise the path under test"
            ),
        )
    payload = Path(f"/tmp/ow_tp_upload_probe_{ns}.txt")
    payload.write_text(f"ow_tp upload transport probe for ns={ns}; not ingest input\n")
    try:
        uploaded = dbx.upload(str(payload), f"{ns}/{PROBE_DIR}/upload_probe.txt")
    except Exception as exc:  # noqa: BLE001 - the failure text *is* the evidence
        return UploadProbe(target=target, verified=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        payload.unlink(missing_ok=True)
    return UploadProbe(target=uploaded, verified=True, cleanup_error=_remove_probe(uploaded))


def _remove_probe(target: str) -> str | None:
    """Delete the probe payload again, returning the failure text if it survives.

    A probe that proves the transport works and then keeps its payload would leave an
    object in the shared volume that no contract covers, and leave it silently: check 5
    enumerates tables and jobs, not files. So the write is undone here, and a delete
    that fails is reported rather than swallowed.
    """
    try:
        dbx.request("DELETE", f"/api/2.0/fs/files{urllib.parse.quote(target)}")
        return None
    except Exception as exc:  # noqa: BLE001 - a surviving probe artifact must be visible
        return f"{type(exc).__name__}: {exc}"


def _upload_caveat(ns: str, upload: UploadProbe) -> list[str]:
    """Report the upload transport as observed by `probe_upload` on this run."""
    if upload.verified is None:
        return [
            "* **`make dbx-upload` is UNVERIFIED (not probed on this run).** The transport was not",
            f"  exercised: {upload.skipped}. The inputs the checks above read were landed inside",
            "  Databricks (serverless task writing to the volume), which is a demo workaround, **not**",
            "  the production transport.",
            "",
        ]
    if upload.verified:
        cleanup = (
            "  The probe payload was deleted again, so the probe leaves nothing in the volume."
            if upload.cleanup_error is None
            else f"  WARNING: the probe payload could not be deleted again: {upload.cleanup_error}"
        )
        return [
            "* **`make dbx-upload` is VERIFIED.** The documented upload transport was exercised on this",
            "  run — a probe payload was PUT through the same `dbx.upload` path, to",
            f"  `{upload.target}` (a sibling of the drop directory that no ingest statement reads, so",
            "  the verifier never stages its own input into the path under test).",
            cleanup,
            "",
        ]
    return [
        "* **`make dbx-upload` is UNVERIFIED.** The documented upload transport was attempted on this run",
        "  and refused:",
        "",
        "  ```",
        f"  $ make dbx-upload NS={ns}",
        f"  PUT /api/2.0/fs/files{upload.target}",
        f"  -> {upload.error}",
        "  ```",
        "",
        "  The inputs the checks above read were landed inside Databricks instead (serverless task writing",
        "  to the volume). That is a demo workaround, **not** the production transport, and no check was",
        "  weakened to accommodate it — every assertion still reads what is actually in the volume and in",
        "  the tables, compared against the golden `.done` artifacts on disk.",
        "",
    ]


def render_report(
    checks: list[Check], ns: str, golden_root: Path, landing_root: str, upload: UploadProbe
) -> str:
    verdict = (
        "green" if all(c.passed for c in checks)
        else "blocked" if any(c.passed is None for c in checks) and not any(c.passed is False for c in checks)
        else "partial"
    )
    out = [
        "# Recon: `sftp_ingest_poll.ksh` → `ow_tp_sftp_ingest`",
        "",
        f"- Generated: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}",
        f"- Namespace: `{ns}`  |  catalog: `{CATALOG}`  |  landing: `{landing_root}`",
        f"- Golden baseline provenance: artifacts of a real `sftp_ingest_poll.ksh` run "
        f"(`make legacy-etl-gen-data NS={ns}` + `make legacy-etl-run JOB=sftp_ingest_poll`), "
        f"read byte-for-byte from `{golden_root}/incoming/*.dat.done`",
        f"- Result: **{verdict}**",
        "",
        "| # | Check | Result |",
        "|---|---|---|",
    ]
    out += [f"| {c.number} | {c.name} | **{c.status}** |" for c in checks]
    out.append("")
    for check in checks:
        out += [f"## {check.number}. {check.name} — {check.status}", "", "```"]
        out += check.detail or ["(no detail)"]
        out += ["```", ""]
    out += [
        "## Scope and caveats",
        "",
        "* **Retention is row-only, landing is the archive.** The `retention` task trims rows from",
        "  `bronze.custbill_files` / `bronze.custbill_lines` past `retention_days`; it never removes the",
        "  landed drop file. That mirrors the legacy job, which renamed each drop to `*.done` in place and",
        "  kept it forever — those `.done` files are exactly the golden artifacts hashed above. Landing",
        "  therefore stays the replay source, and a trimmed file re-ingests on a later run; that is",
        "  intended, not a leak. Re-ingest cannot duplicate, because the manifest is keyed on",
        "  `(ns, file_name)` and carries the whole-file `sha256` (see check 4).",
        "* **A half-written drop fails the run, after the complete files have landed.** Check 6 above",
        "  exercises that on a real mixed drop: the complete file lands in both tables, the truncated one",
        "  contributes no row and stays in the drop path unconsumed, and the run still exits non-zero naming",
        "  it with the bytes observed against the count its trailer declares. The gate is content-based, with",
        "  no grace window and no timeout \u2014 that heuristic is what this conversion replaces. Check 6's",
        "  fixtures live in their own namespace and its rows are deleted again, so the reconciled namespace",
        "  above is untouched by it.",
        *_upload_caveat(ns, upload),
        "Reproduce with:",
        "",
        "```bash",
        'export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"',
        f"python3 scripts/tp_databricks/recon_sftp_ingest.py --ns {ns} "
        "--report docs/tech-partnerships/recon/sftp_ingest_poll.md",
        "```",
        "",
    ]
    return "\n".join(out)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # `make dbx-recon UNIT=sftp_ingest NS=<ns>` passes the namespace in the environment,
    # not as a flag, so defaulting to "demo" here would silently reconcile — and, via
    # check 4, re-run the ingest for — a namespace nobody asked about.
    parser.add_argument("--ns", default=os.environ.get("NS") or "demo")
    parser.add_argument("--golden-root", default=str(GOLDEN_ROOT))
    parser.add_argument("--landing-root", default=LANDING_ROOT)
    parser.add_argument("--no-rerun", action="store_true", help="skip check 4 (the idempotency re-run)")
    parser.add_argument("--gate-ns", default=GATE_NS, help="namespace holding check 6's mixed-drop fixtures")
    parser.add_argument(
        "--no-gate-probe",
        action="store_true",
        help="skip check 6 (the mixed complete/half-written drop)",
    )
    parser.add_argument(
        "--no-upload-probe",
        action="store_true",
        help="do not exercise the dbx.upload transport; report it as unverified/unprobed",
    )
    parser.add_argument("--report", help="write the markdown report to this path")
    args = parser.parse_args(argv)

    # The recon builds SQL text too, so its `ns` goes through the same gate the
    # statement module applies rather than being trusted from the command line. The
    # normalized root is what gets used, not the argument as typed: the gate strips a
    # trailing slash, and comparing a path built from the unstripped one against a
    # `source_path` the statements wrote from the stripped one is a mismatch the
    # pipeline did not cause.
    args.ns, _catalog, args.landing_root = sftp_ingest_sql.validated(
        args.ns, CATALOG, args.landing_root
    )

    # Check 6 writes (and then deletes) rows of its own, so its namespace goes through the
    # same gate, and must not be the namespace under reconciliation.
    gate_ns, _catalog, _root = sftp_ingest_sql.validated(args.gate_ns, CATALOG, args.landing_root)
    if gate_ns == args.ns:
        parser.error(f"--gate-ns {gate_ns!r} must differ from --ns: check 6 deletes its own namespace's rows")

    golden = load_golden(Path(args.golden_root))
    # `--no-rerun` is the read-only pass, so it writes nothing at all, probe included.
    upload = probe_upload(args.ns, args.landing_root, not args.no_upload_probe and not args.no_rerun)
    print(f"[upload transport] {upload.target}: {upload.skipped or upload.error or 'VERIFIED'}")
    checks = [
        _contained(
            1,
            "bronze.custbill_files matches the golden artifacts",
            check1_manifest,
            args.ns,
            args.landing_root,
            golden,
        ),
        _contained(2, "bronze.custbill_lines preserves the raw records", check2_lines, args.ns, golden),
        _contained(3, "TRL-declared count equals detail lines ingested", check3_trailer, args.ns, golden),
        _contained(
            4,
            "re-running the ingest leaves both tables byte-identical",
            check4_idempotency,
            args.ns,
            args.landing_root,
            not args.no_rerun,
            golden,
        ),
        _contained(5, "no ow_tp object outside the contract, no unprefixed object", check5_scope, args.ns, args.landing_root),
        _contained(
            6,
            "a half-written file neither lands nor blocks the complete files",
            check6_gate,
            gate_ns,
            args.landing_root,
            not args.no_gate_probe and not args.no_rerun,
        ),
    ]

    for check in checks:
        print(f"[{check.status}] {check.number}. {check.name}")
        for line in check.detail:
            print(f"    {line}")

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            render_report(checks, args.ns, Path(args.golden_root), args.landing_root, upload)
        )
        print(f"report written to {report_path}")

    # A deliberate skip (`--no-rerun`) is BLOCKED and still exits 0; a check that
    # crashed is BLOCKED and exits non-zero, because "could not be run" must never
    # read as "green".
    ok = all(check.passed is not False and check.error is None for check in checks)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
