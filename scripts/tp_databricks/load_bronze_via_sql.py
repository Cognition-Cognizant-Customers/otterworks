#!/usr/bin/env python3
"""Fallback transport: load an extract into bronze over SQL instead of the landing volume.

The pipeline's normal path is
`extract_search_sources.py --upload` -> `/Volumes/ow_tp/bronze/landing/<ns>/<prefix>/`
-> the `ingest_bronze` notebook task. That path needs the Databricks Files API, and the
demo token available to this unit has no `files` scope:

    PUT /api/2.0/fs/files/Volumes/ow_tp/bronze/landing/... -> 403:
    {"error_code":403,"message":"Provided access token does not have required scopes: files"}

This loader writes the *same* envelopes to the *same* bronze table through the serverless
SQL warehouse so the rest of the pipeline (projection, reconciliation, build-then-swap) can
be exercised and reconciled. It substitutes only the transport; use the volume path whenever
a files-scoped token is available, and say which one was used in the recon report.

Payloads are base64-encoded into the statement and decoded server side, so no source content
is altered by SQL string escaping.

Usage:
    load_bronze_via_sql.py <extract_dir> --ns demo [--batch-size 250]
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx  # noqa: E402

BRONZE_TABLE = f"{dbx.CATALOG}.bronze.search_documents_raw"
FILES = ("documents.ndjson", "files.ndjson")


def normalize_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value).astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def row_literal(envelope: dict) -> str:
    payload_b64 = base64.b64encode(envelope["payload"].encode()).decode()
    entity_id = envelope["entity_id"].replace("\\", "\\\\").replace("'", "\\'")
    return (
        "('{ns}', '{entity_type}', '{entity_id}', CAST(unbase64('{payload}') AS STRING), "
        "TIMESTAMP '{extracted_at}')".format(
            ns=envelope["ns"],
            entity_type=envelope["entity_type"],
            entity_id=entity_id,
            payload=payload_b64,
            extracted_at=normalize_timestamp(envelope["extracted_at"]),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extract_dir")
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[a-z0-9_]+", args.ns):
        raise SystemExit(f"--ns must match [a-z0-9_]+, got {args.ns!r}")

    extract_dir = Path(args.extract_dir)
    manifest = json.loads((extract_dir / "_manifest.json").read_text())
    if manifest["ns"] != args.ns:
        raise SystemExit(f"manifest ns {manifest['ns']!r} does not match --ns {args.ns!r}")

    envelopes = []
    for name in FILES:
        with (extract_dir / name).open(encoding="utf-8") as handle:
            for line in handle:
                envelope = json.loads(line)
                if envelope["ns"] != args.ns:
                    raise SystemExit(f"{name} contains ns {envelope['ns']!r}, expected {args.ns!r}")
                envelopes.append(envelope)

    expected = {entity: int(count) for entity, count in manifest["counts"].items()}
    landed = {}
    for envelope in envelopes:
        landed[envelope["entity_type"]] = landed.get(envelope["entity_type"], 0) + 1
    if landed != expected:
        raise SystemExit(f"extract files {landed} do not match the manifest {expected}")

    # Idempotent per namespace, same guarantee as the notebook's replaceWhere.
    dbx.sql(f"DELETE FROM {BRONZE_TABLE} WHERE ns = '{args.ns}'")
    for start in range(0, len(envelopes), args.batch_size):
        batch = envelopes[start:start + args.batch_size]
        values = ",\n".join(row_literal(envelope) for envelope in batch)
        dbx.sql(f"INSERT INTO {BRONZE_TABLE} VALUES\n{values}")
        print(f"inserted {start + len(batch)}/{len(envelopes)} rows", flush=True)

    rows = dbx.sql(
        f"SELECT entity_type, COUNT(*), COUNT(DISTINCT entity_id) FROM {BRONZE_TABLE} "
        f"WHERE ns = '{args.ns}' GROUP BY entity_type ORDER BY entity_type"
    )
    loaded = {row[0]: {"rows": int(row[1]), "distinct_entity_ids": int(row[2])} for row in rows}
    print(json.dumps({"ns": args.ns, "manifest": expected, "bronze": loaded}, indent=2))
    if {entity: counts["rows"] for entity, counts in loaded.items()} != expected:
        print("bronze counts do not match the extract manifest", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
