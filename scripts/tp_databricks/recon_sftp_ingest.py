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

Usage:
    python3 recon_sftp_ingest.py --ns demo                      # full run
    python3 recon_sftp_ingest.py --ns demo --no-rerun           # skip check 4
    python3 recon_sftp_ingest.py --ns demo --report out.md      # + markdown report
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dbx  # noqa: E402
import sftp_ingest_sql  # noqa: E402

GOLDEN_ROOT = Path("/home/ubuntu/tp-golden/custbill")
LANDING_ROOT = "/Volumes/ow_tp/bronze/landing"
CATALOG = "ow_tp"

# Objects this unit is allowed to create / write. Anything else in the shared
# workspace belongs to another unit or to the parent, and is out of bounds.
OWNED_TABLES = {"ow_tp.bronze.custbill_files", "ow_tp.bronze.custbill_lines"}
OWNED_JOB = "ow_tp_sftp_ingest"

_QUALIFIED_NAME = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b")


@dataclass
class Check:
    number: int
    name: str
    passed: bool | None = None  # None == could not be run (blocked)
    detail: list[str] = field(default_factory=list)

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
            lines=text.rstrip("\n").split("\n"),
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


def check1_manifest(ns: str, golden: dict[str, GoldenFile]) -> Check:
    """Manifest == golden: same files, same byte counts, same SHA-256."""
    check = Check(1, "bronze.custbill_files matches the golden artifacts")
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
        ok = ok and size_ok and hash_ok
        check.detail.append(
            f"{name}: size_bytes converted={got['size_bytes']} golden={want.size_bytes} "
            f"[{'ok' if size_ok else 'MISMATCH'}]"
        )
        check.detail.append(
            f"{name}: sha256 converted={got['sha256']} golden={want.sha256} [{'ok' if hash_ok else 'MISMATCH'}]"
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
       max(CASE WHEN raw_line LIKE 'TRL%' THEN CAST(substr(raw_line, 4, 10) AS BIGINT) END) AS declared,
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


def check4_idempotency(ns: str, landing_root: str, rerun: bool) -> Check:
    """Re-run the identical statement set; both tables must be unchanged."""
    check = Check(4, "re-running the ingest leaves both tables byte-identical")
    if not rerun:
        check.detail.append("skipped (--no-rerun)")
        return check
    before = _snapshot(ns)
    sftp_ingest_sql.run(ns=ns, catalog=CATALOG, landing_root=landing_root, create_tables=False)
    after = _snapshot(ns)
    check.passed = before == after
    for table in sorted(before):
        same = before[table] == after[table]
        check.detail.append(
            f"{table}: rows:sha256 before={before[table]} after={after[table]} [{'unchanged' if same else 'CHANGED'}]"
        )
    return check


def check5_scope(ns: str) -> Check:
    """Nothing outside the two contracted tables is created or touched."""
    check = Check(5, "no ow_tp object outside the contract, no unprefixed object")
    ok = True

    # (a) static: every three-part identifier in the statement set this unit executes.
    statements = [
        *sftp_ingest_sql.ddl_statements(CATALOG),
        sftp_ingest_sql.incomplete_files_query(ns, CATALOG, LANDING_ROOT),
        *sftp_ingest_sql.ingest_statements(ns, CATALOG, LANDING_ROOT),
    ]
    retention = Path(__file__).resolve().parents[2] / "infrastructure" / "terraform-databricks"
    retention_sql = (retention / "sql" / "sftp_ingest_retention.sql").read_text()
    referenced = {name for s in statements for name in _QUALIFIED_NAME.findall(s)}
    unowned = referenced - OWNED_TABLES
    unprefixed = {name for name in referenced if not name.startswith("ow_tp.")}
    ok = ok and not unowned and not unprefixed
    check.detail.append(f"tables referenced by the statement set: {sorted(referenced) or '[]'}")
    check.detail.append(f"outside the contract: {sorted(unowned) or 'none'}")
    check.detail.append(f"unprefixed: {sorted(unprefixed) or 'none'}")
    # retention SQL addresses its tables through IDENTIFIER(:catalog || ...), so match those
    retention_tables = {f"ow_tp{frag}" for frag in re.findall(r":catalog \|\| '(\.[a-z_.]+)'", retention_sql)}
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

    found = dbx.inventory()
    strays = [name for name in found["jobs"] if name.startswith("ow_tp_dev_")]
    ok = ok and not strays
    check.detail.append(f"ow_tp jobs in the workspace: {sorted(found['jobs']) or 'none (1/3 not applied yet)'}")
    check.detail.append(f"throwaway ow_tp_dev_* jobs left behind: {strays or 'none'}")
    check.detail.append(f"catalogs={found['catalogs']} secret_scopes={found['secret_scopes']} dirs={found['directories']}")
    check.passed = ok
    return check


def render_report(checks: list[Check], ns: str, golden_root: Path, landing_root: str) -> str:
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
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--golden-root", default=str(GOLDEN_ROOT))
    parser.add_argument("--landing-root", default=LANDING_ROOT)
    parser.add_argument("--no-rerun", action="store_true", help="skip check 4 (the idempotency re-run)")
    parser.add_argument("--report", help="write the markdown report to this path")
    args = parser.parse_args(argv)

    golden = load_golden(Path(args.golden_root))
    checks = [
        check1_manifest(args.ns, golden),
        check2_lines(args.ns, golden),
        check3_trailer(args.ns, golden),
        check4_idempotency(args.ns, args.landing_root, rerun=not args.no_rerun),
        check5_scope(args.ns),
    ]

    for check in checks:
        print(f"[{check.status}] {check.number}. {check.name}")
        for line in check.detail:
            print(f"    {line}")

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(checks, args.ns, Path(args.golden_root), args.landing_root))
        print(f"report written to {report_path}")

    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
