#!/usr/bin/env python3
"""Bootstrap the bronze CUSTBILL tables this unit reads.

`ow_tp.bronze.custbill_files` / `custbill_lines` belong to the `ow_tp_sftp_ingest`
unit (docs/tech-partnerships/contracts/sftp_ingest_poll.md). They did not exist
in the shared workspace when the parse unit was converted, so this script stands
them up from the same legacy landing files, using that contract's schema
verbatim, so the parse conversion can be run and reconciled end to end. It is a
bootstrap, not a reimplementation of the ingest job: it only ever creates tables
`IF NOT EXISTS` and only ever rewrites rows for its own namespace.

The manifest columns are taken from the legacy artifact itself (`size_bytes`,
`sha256` of the ingested `.dat`), which is what the ingest contract requires:
hash equality against the legacy file, not a recomputation of a copy.

Usage:
    NS=demo python3 scripts/tp_databricks/load_custbill_bronze.py [--source DIR]

DIR defaults to $OTTERWORKS_LEGACY_ROOT/incoming (default /tmp/otterworks-legacy),
where the legacy ingest job leaves `CUSTBILL_*.dat.done`.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "databricks" / "notebooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import custbill_sql  # noqa: E402  (same-directory helper, imported after path setup)
import dbx  # noqa: E402

VOLUME_RELDIR = "{ns}/custbill"


def _sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def source_files(source_dir: Path, ns: str) -> list[Path]:
    """Legacy landing files for this namespace, `.done`-renamed or not."""
    pattern = f"CUSTBILL_{ns.upper()}_*.dat"
    files = sorted(source_dir.glob(pattern)) + sorted(source_dir.glob(pattern + ".done"))
    return [f for f in files if f.is_file()]


def file_manifest(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    lines = raw.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return {
        "file_name": path.name[: -len(".done")] if path.name.endswith(".done") else path.name,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        # The ingest unit's manifest record_count includes every landed line.
        "record_count": len(lines),
        "lines": lines,
    }


def upload_to_landing(path: Path, ns: str, manifest: dict[str, object]) -> str:
    """Best-effort upload of the raw drop into the landing volume.

    The volume is the ingest unit's target and is not required for the parse
    conversion, so a permission failure here is reported and recorded in
    `source_path` rather than aborting the load.
    """
    relpath = f"{VOLUME_RELDIR.format(ns=ns)}/{manifest['file_name']}"
    try:
        return dbx.upload(str(path), relpath)
    except dbx.DatabricksError as exc:
        print(f"  volume upload skipped ({exc})", file=sys.stderr)
        return f"local:{path}"


def load(ns: str, source_dir: Path) -> int:
    files = source_files(source_dir, ns)
    if not files:
        print(f"no CUSTBILL_{ns.upper()}_*.dat[.done] files under {source_dir}", file=sys.stderr)
        return 1

    for statement in custbill_sql.bronze_bootstrap_ddl():
        dbx.sql(statement)

    manifests = []
    for path in files:
        manifest = file_manifest(path)
        manifest["source_path"] = upload_to_landing(path, ns, manifest)
        manifests.append(manifest)
        print(
            f"  {manifest['file_name']}: {manifest['size_bytes']} bytes, "
            f"{manifest['record_count']} landed lines, sha256 {manifest['sha256']}"
        )

    names = ", ".join(_sql_literal(str(m["file_name"])) for m in manifests)
    dbx.sql(
        f"DELETE FROM {custbill_sql.BRONZE_FILES} "
        f"WHERE ns = {_sql_literal(ns)} AND file_name IN ({names})"
    )
    dbx.sql(
        f"DELETE FROM {custbill_sql.BRONZE_LINES} "
        f"WHERE ns = {_sql_literal(ns)} AND file_name IN ({names})"
    )

    values = ", ".join(
        "({ns}, {name}, {size}, {sha}, {count}, current_timestamp(), {src})".format(
            ns=_sql_literal(ns),
            name=_sql_literal(str(m["file_name"])),
            size=m["size_bytes"],
            sha=_sql_literal(str(m["sha256"])),
            count=m["record_count"],
            src=_sql_literal(str(m["source_path"])),
        )
        for m in manifests
    )
    dbx.sql(
        f"INSERT INTO {custbill_sql.BRONZE_FILES} "
        "(ns, file_name, size_bytes, sha256, record_count, ingested_at, source_path) "
        f"VALUES {values}"
    )

    line_values = ", ".join(
        "({ns}, {name}, {line_no}, {raw})".format(
            ns=_sql_literal(ns),
            name=_sql_literal(str(m["file_name"])),
            line_no=index,
            raw=_sql_literal(line),
        )
        for m in manifests
        for index, line in enumerate(m["lines"], start=1)
    )
    dbx.sql(
        f"INSERT INTO {custbill_sql.BRONZE_LINES} (ns, file_name, line_no, raw_line) "
        f"VALUES {line_values}"
    )

    total = sum(len(m["lines"]) for m in manifests)
    print(f"bronze loaded for ns={ns}: {len(manifests)} files, {total} raw lines")
    return 0


def main() -> int:
    default_root = os.environ.get("OTTERWORKS_LEGACY_ROOT", "/tmp/otterworks-legacy")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--source", default=str(Path(default_root) / "incoming"))
    args = parser.parse_args()
    return load(args.ns, Path(args.source))


if __name__ == "__main__":
    sys.exit(main())
