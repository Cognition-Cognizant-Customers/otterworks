#!/usr/bin/env python3
"""Statement set for `ow_tp_sftp_ingest` — the converted `sftp_ingest_poll.ksh`.

Single source of truth for the bronze ingest, imported by three callers so they
can never drift apart:

* the job's notebook task (`databricks/notebooks/sftp_ingest_bronze.py`), which
  imports it from `/Shared/ow_tp/sftp_ingest_sql.py`;
* this module's own CLI, which runs the identical statements on the serverless
  SQL warehouse through `dbx.py` (`python3 -m sftp_ingest_sql run --ns demo`);
* the recon script, which asserts against what those statements produced.

What replaces what, relative to the legacy ksh job:

* the "settle" heuristic (`wc -c` twice, one second apart) is replaced by a
  content handshake: every landed file is read whole, hashed (SHA-256), and only
  ingested when it is *structurally complete* — exactly one HDR record, exactly
  one TRL record, and a TRL-declared record count equal to the detail lines
  present. A half-written file fails that gate and is not ingested;
* `.done` renames and a never-removed lock file are replaced by MERGEs keyed on
  `(ns, file_name, line_no)` and `(ns, file_name)`, so re-running is a no-op;
* hostname if-blocks are replaced by the `landing_root` parameter.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DDL_FILE = (
    Path(__file__).resolve().parents[2]
    / "infrastructure"
    / "terraform-databricks"
    / "sql"
    / "sftp_ingest_bronze_tables.sql"
)


def default_landing_root(catalog: str) -> str:
    """The landing volume of `catalog`, so the read side follows the write side.

    `dbx.upload` derives its target from the catalog too, and the job passes a
    landing_root derived from `var.catalog_name`; a constant here would read one
    estate's volume while merging into another's tables.
    """
    return f"/Volumes/{catalog}/bronze/landing"


# `\Z`, not `$`: Python's `$` also matches before a trailing newline, so `$` would
# let 'demo\n' through a gate whose whole job is to bound what reaches SQL text.
_NS_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,30}\Z")
_CATALOG_RE = re.compile(r"^ow_tp[a-z0-9_]*\Z")
# A Unity Catalog volume path under an ow_tp* catalog, and nothing else. No `.`
# anywhere, so `/Volumes/ow_tp/bronze/landing/../../other_catalog/...` cannot pass
# the gate and read another demo's volume — an absolute-path check alone would.
_ROOT_RE = re.compile(r"^/Volumes/ow_tp[a-z0-9_]*(/[A-Za-z0-9_-]+)+\Z")


def _validated_catalog(catalog: str) -> str:
    """Gate a catalog on its own, for statements that touch no landing path."""
    if not _CATALOG_RE.match(catalog):
        raise ValueError(f"catalog {catalog!r} must match {_CATALOG_RE.pattern} (shared workspace)")
    return catalog


def _validated(ns: str, catalog: str, landing_root: str) -> tuple[str, str, str]:
    """Reject anything that could not be a namespace / catalog / volume path.

    These values are interpolated into SQL text, so they are validated rather
    than trusted: the legacy estate's habit of pasting shell variables into
    commands is exactly what this conversion is retiring.
    """
    if not _NS_RE.match(ns):
        raise ValueError(f"ns {ns!r} must match {_NS_RE.pattern}")
    _validated_catalog(catalog)
    if not _ROOT_RE.match(landing_root.rstrip("/")):
        raise ValueError(
            f"landing_root {landing_root!r} must be an ow_tp volume path matching {_ROOT_RE.pattern}"
        )
    # The volume read from and the tables written to must belong to the same estate:
    # crossing them would copy one catalog's drops into another catalog's bronze.
    if not landing_root.rstrip("/").startswith(f"/Volumes/{catalog}/"):
        raise ValueError(
            f"landing_root {landing_root!r} is not a volume of catalog {catalog!r} "
            f"(expected a path under /Volumes/{catalog}/)"
        )
    return ns, catalog, landing_root.rstrip("/")


def validated(ns: str, catalog: str, landing_root: str) -> tuple[str, str, str]:
    """Public entry point to the same gate, for callers building their own SQL."""
    return _validated(ns, catalog, landing_root)


def _source_cte(ns: str, landing_root: str) -> str:
    """CTEs over the landed files: whole-file bytes, exploded lines, audit, gate.

    `wholeText => true` keeps each file as one value, which is what makes the
    hash comparable to `sha256sum` on the legacy artifact and the line numbering
    faithful to delivery order.
    """
    return f"""
WITH raw AS (
  SELECT
    _metadata.file_path                                       AS source_path,
    regexp_extract(_metadata.file_path, '([^/]+)$', 1)         AS file_name,
    value                                                     AS content,
    -- the record body: exactly one trailing line terminator removed, so the last
    -- record is not mistaken for a 53rd empty one. Not a regex: Java's `$` also
    -- matches before a final terminator, so '\\n$' would eat a blank last record.
    CASE WHEN endswith(value, '\\n') THEN left(value, length(value) - 1) ELSE value END AS body
  -- An existing-but-empty drop directory is a clean no-op. A directory that does not
  -- exist fails the run (CF_PATH_DOES_NOT_EXIST_FOR_READ_FILES), deliberately: an
  -- absent drop path means the namespace was never provisioned or the transport is
  -- writing somewhere else, and reporting that as "no files today" is exactly the
  -- `2>/dev/null || true` habit this conversion is retiring.
  FROM read_files('{landing_root}/{ns}/custbill/', format => 'text', wholeText => true)
),
lines AS (
  SELECT raw.source_path, raw.file_name, l.pos + 1 AS line_no, l.line AS raw_line
  FROM raw
  LATERAL VIEW posexplode(split(raw.body, '\\n')) l AS pos, line
),
audit AS (
  SELECT
    file_name,
    count_if(raw_line LIKE 'HDR%')                                      AS header_lines,
    count_if(raw_line LIKE 'TRL%')                                      AS trailer_lines,
    count_if(raw_line NOT LIKE 'HDR%' AND raw_line NOT LIKE 'TRL%')     AS detail_lines,
    -- TRY_CAST: a transfer cut off mid-trailer leaves a non-numeric count, and under
    -- ANSI mode a plain CAST would abort the very query meant to report that file as
    -- incomplete. NULL fails `detail_lines = trailer_declared`, which is the point.
    max(CASE WHEN raw_line LIKE 'TRL%' THEN TRY_CAST(substr(raw_line, 4, 10) AS BIGINT) END) AS trailer_declared
  FROM lines
  GROUP BY file_name
),
complete AS (
  SELECT file_name
  FROM audit
  WHERE header_lines = 1 AND trailer_lines = 1 AND detail_lines = trailer_declared
)"""


def incomplete_files_query(ns: str, catalog: str, landing_root: str) -> str:
    """Files that fail the completeness handshake; a non-empty result fails the run."""
    ns, catalog, landing_root = _validated(ns, catalog, landing_root)
    return f"""{_source_cte(ns, landing_root)}
SELECT file_name, header_lines, trailer_lines, detail_lines, trailer_declared
FROM audit
WHERE file_name NOT IN (SELECT file_name FROM complete)
ORDER BY file_name"""


def merge_lines(ns: str, catalog: str, landing_root: str) -> str:
    """Raw record lines, byte-faithful, deduped on (ns, file_name, line_no)."""
    ns, catalog, landing_root = _validated(ns, catalog, landing_root)
    return f"""{_source_cte(ns, landing_root)}
MERGE INTO {catalog}.bronze.custbill_lines AS t
USING (
  SELECT '{ns}' AS ns, lines.file_name, lines.line_no, lines.raw_line
  FROM lines
  JOIN complete USING (file_name)
) AS s
ON t.ns = s.ns AND t.file_name = s.file_name AND t.line_no = s.line_no
WHEN MATCHED AND t.raw_line <> s.raw_line THEN UPDATE SET t.raw_line = s.raw_line
WHEN NOT MATCHED THEN INSERT (ns, file_name, line_no, raw_line) VALUES (s.ns, s.file_name, s.line_no, s.raw_line)"""


def prune_lines(ns: str, catalog: str, landing_root: str) -> str:
    """Drop tail records left behind when a file is re-delivered *shorter*.

    Without this, the orphaned high `line_no` rows survive the MERGE, the manifest's
    `record_count` no longer matches the lines present, and the run's own
    reconciliation then fails on this and every later run. Scoped to this `ns` and to
    the files in this drop, so other namespaces and previously ingested files are
    untouched (a `NOT MATCHED BY SOURCE` clause cannot express that: Delta rejects
    subqueries in a MERGE delete condition).
    """
    ns, catalog, landing_root = _validated(ns, catalog, landing_root)
    cte = _source_cte(ns, landing_root)
    return f"""DELETE FROM {catalog}.bronze.custbill_lines
WHERE ns = '{ns}'
  AND file_name IN (SELECT file_name FROM ({cte}
SELECT file_name FROM complete))
  AND concat(file_name, ':', line_no) NOT IN (SELECT concat(file_name, ':', line_no) FROM ({cte}
SELECT lines.file_name, lines.line_no FROM lines JOIN complete USING (file_name)))"""


def merge_files(ns: str, catalog: str, landing_root: str) -> str:
    """The manifest row per file: size, SHA-256, line count, provenance.

    `ingested_at` is only written when the row is new or its content changed, so
    a re-run leaves the table byte-identical.
    """
    ns, catalog, landing_root = _validated(ns, catalog, landing_root)
    return f"""{_source_cte(ns, landing_root)}
MERGE INTO {catalog}.bronze.custbill_files AS t
USING (
  SELECT
    '{ns}'                                                            AS ns,
    raw.file_name,
    octet_length(raw.content)                                         AS size_bytes,
    sha2(encode(raw.content, 'utf-8'), 256)                           AS sha256,
    size(split(raw.body, '\\n'))                                      AS record_count,
    current_timestamp()                                               AS ingested_at,
    raw.source_path
  FROM raw
  JOIN complete USING (file_name)
) AS s
ON t.ns = s.ns AND t.file_name = s.file_name
WHEN MATCHED AND t.sha256 <> s.sha256 THEN UPDATE SET
  t.size_bytes = s.size_bytes, t.sha256 = s.sha256, t.record_count = s.record_count,
  t.ingested_at = s.ingested_at, t.source_path = s.source_path
WHEN NOT MATCHED THEN INSERT (ns, file_name, size_bytes, sha256, record_count, ingested_at, source_path)
VALUES (s.ns, s.file_name, s.size_bytes, s.sha256, s.record_count, s.ingested_at, s.source_path)"""


def _split_statements(text: str) -> list[str]:
    """Split SQL on statement terminators only, ignoring quotes and `--` comments.

    A bare `text.split(';')` cuts the DDL apart inside a column COMMENT literal (the
    `ingested_at` comment contains a semicolon), producing two unterminated fragments
    that fail to parse. Databricks parses the .sql file itself for the job's SQL task,
    so this only ever broke the shared-module CLI — which is the path the recon and
    `python3 -m sftp_ingest_sql run` use, i.e. the one the evidence comes from.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    in_comment = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_comment:
            in_comment = char != "\n"
        elif in_string:
            # '' is an escaped quote inside a literal, not the end of one.
            if char == "'" and text[index + 1 : index + 2] == "'":
                current.append(char)
                index += 1
            else:
                in_string = char != "'"
        elif char == "'":
            in_string = True
        elif char == "-" and text[index + 1 : index + 2] == "-":
            in_comment = True
        elif char == ";":
            statements.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip() and not _only_comments(s)]


def ddl_statements(catalog: str) -> list[str]:
    """The bronze DDL, read from the same .sql file the job's SQL task runs."""
    catalog = _validated_catalog(catalog)
    text = DDL_FILE.read_text()
    text = text.replace(":catalog || '.", f"'{catalog}.")  # bind :catalog for direct execution
    statements = _split_statements(text)
    # This file holds nothing but CREATE TABLEs, so anything else parsed out of it is a
    # fragment of one — the shape a bad split takes — and must not reach the warehouse.
    bad = [s for s in statements if not _body(s).upper().startswith("CREATE TABLE")]
    if not statements or bad:
        raise ValueError(
            f"{DDL_FILE.name} did not parse into CREATE TABLE statements "
            f"({len(statements)} parsed, {len(bad)} unrecognized); refusing to execute it"
        )
    return statements


def _body(statement: str) -> str:
    """The statement without its leading comment lines."""
    lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
    return "\n".join(lines).strip()


def _only_comments(statement: str) -> bool:
    return all(not line.strip() or line.strip().startswith("--") for line in statement.splitlines())


def ingest_statements(ns: str, catalog: str, landing_root: str) -> list[str]:
    """Lines, prune of any re-delivery leftovers, then the manifest. In this order.

    The completeness gate runs before any of these; a file that fails it is absent
    from `complete` and therefore invisible to all three statements.
    """
    return [
        merge_lines(ns, catalog, landing_root),
        prune_lines(ns, catalog, landing_root),
        merge_files(ns, catalog, landing_root),
    ]


class MissingDropPathError(RuntimeError):
    """The namespace's drop directory does not exist, so the run read nothing."""


def drop_path(ns: str, landing_root: str) -> str:
    """The directory the transport delivers this namespace's CUSTBILL files to."""
    return f"{landing_root}/{ns}/custbill/"


def landed_files_query(ns: str, catalog: str, landing_root: str) -> str:
    """File names the ingest's own source scan sees, in delivery-name order."""
    ns, _catalog, landing_root = _validated(ns, catalog, landing_root)
    return (
        "SELECT DISTINCT regexp_extract(_metadata.file_path, '([^/]+)$', 1) AS file_name "
        f"FROM read_files('{drop_path(ns, landing_root)}', format => 'text', wholeText => true) "
        "ORDER BY file_name"
    )


def as_missing_drop_path(exc: Exception, ns: str, landing_root: str) -> MissingDropPathError | None:
    """Translate an absent-drop-path failure into one that names ns and the path.

    Two conditions the legacy poller conflated: an existing but empty directory is
    nothing to do today, while a directory that does not exist means this namespace
    was never staged (or the transport writes elsewhere) and the run has not
    succeeded. Only the second is an error, and it should say which path it wanted
    rather than leaving the operator to decode a source-format error code.
    """
    if "CF_PATH_DOES_NOT_EXIST_FOR_READ_FILES" not in str(exc):
        return None
    return MissingDropPathError(
        f"drop path for ns={ns!r} does not exist: {drop_path(ns, landing_root)} — "
        "the namespace was never staged, or the transport is writing somewhere else. "
        "An existing but empty drop directory is a no-op; an absent one is not."
    )


def _over_landing(dbx, statement: str, ns: str, landing_root: str) -> list[list[str]]:
    """Run a statement that reads the drop path, naming the path if it is absent."""
    try:
        return dbx.sql(statement)
    except Exception as exc:
        missing = as_missing_drop_path(exc, ns, landing_root)
        if missing is not None:
            raise missing from exc
        raise


def run(ns: str, catalog: str, landing_root: str, create_tables: bool = True) -> None:
    """Execute the ingest on the serverless SQL warehouse via dbx.py.

    This is the same statement set the job's notebook task runs; it exists so the
    pipeline (and its recon evidence) can be produced without standing up compute.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import dbx  # noqa: PLC0415 -- resolved from this directory, both locally and in-workspace

    if create_tables:
        for statement in ddl_statements(catalog):
            dbx.sql(statement)

    landed = _over_landing(dbx, landed_files_query(ns, catalog, landing_root), ns, landing_root)
    if not landed:
        print(f"no files under {drop_path(ns, landing_root)}: nothing to ingest (no-op)")
        return

    bad = dbx.sql(incomplete_files_query(ns, catalog, landing_root))
    if bad:
        raise RuntimeError(
            "completeness handshake failed; refusing to ingest half-written files: "
            + "; ".join(
                f"{row[0]} (hdr={row[1]}, trl={row[2]}, detail={row[3]}, declared={row[4]})" for row in bad
            )
        )

    for statement in ingest_statements(ns, catalog, landing_root):
        dbx.sql(statement)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["print", "run"])
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--catalog", default="ow_tp")
    parser.add_argument(
        "--landing-root",
        default=None,
        help="defaults to the landing volume of --catalog",
    )
    args = parser.parse_args(argv)
    if args.landing_root is None:
        args.landing_root = default_landing_root(args.catalog)

    if args.command == "print":
        for statement in [
            *ddl_statements(args.catalog),
            incomplete_files_query(args.ns, args.catalog, args.landing_root),
            *ingest_statements(args.ns, args.catalog, args.landing_root),
        ]:
            print(statement.strip() + ";\n")
        return 0

    run(args.ns, args.catalog, args.landing_root)
    print(f"ingested ns={args.ns} from {args.landing_root}/{args.ns}/custbill/")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
