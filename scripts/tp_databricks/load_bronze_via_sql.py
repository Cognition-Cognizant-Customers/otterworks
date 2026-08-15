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
ENTITY_TYPES = {"document", "file"}
NS_PATTERN = re.compile(r"[a-z0-9_]+")


def normalize_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value).astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def row_literal(envelope: dict) -> str:
    ns = envelope["ns"]
    entity_type = envelope["entity_type"]
    if not NS_PATTERN.fullmatch(ns):
        raise ValueError(f"envelope ns must match [a-z0-9_]+, got {ns!r}")
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"unsupported envelope entity_type {entity_type!r}")
    payload_b64 = base64.b64encode(envelope["payload"].encode()).decode()
    entity_id_b64 = base64.b64encode(str(envelope["entity_id"]).encode()).decode()
    return (
        "('{ns}', '{entity_type}', CAST(unbase64('{entity_id}') AS STRING), "
        "CAST(unbase64('{payload}') AS STRING), "
        "TIMESTAMP '{extracted_at}')".format(
            ns=ns,
            entity_type=entity_type,
            entity_id=entity_id_b64,
            payload=payload_b64,
            extracted_at=normalize_timestamp(envelope["extracted_at"]),
        )
    )


def counts_match_expected(observed: dict[str, int], expected: dict[str, int]) -> bool:
    return set(observed) <= set(expected) and {
        entity: observed.get(entity, 0) for entity in expected
    } == expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extract_dir")
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[a-z0-9_]+", args.ns):
        raise SystemExit(f"--ns must match [a-z0-9_]+, got {args.ns!r}")
    scratch_table = f"{dbx.CATALOG}.bronze.search_documents_raw_load_{args.ns}"

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
    unexpected = set(expected) - ENTITY_TYPES
    if unexpected:
        raise SystemExit(f"manifest contains unsupported entity types: {sorted(unexpected)}")
    landed = {entity: 0 for entity in expected}
    for envelope in envelopes:
        if envelope["entity_type"] not in ENTITY_TYPES:
            raise SystemExit(f"unsupported envelope entity_type {envelope['entity_type']!r}")
        landed[envelope["entity_type"]] = landed.get(envelope["entity_type"], 0) + 1
    if not counts_match_expected(landed, expected):
        raise SystemExit(f"extract files {landed} do not match the manifest {expected}")

    dbx.sql(
        f"CREATE OR REPLACE TABLE {scratch_table} USING DELTA AS "
        f"SELECT * FROM {BRONZE_TABLE} WHERE 1 = 0"
    )
    try:
        for start in range(0, len(envelopes), args.batch_size):
            batch = envelopes[start:start + args.batch_size]
            values = ",\n".join(row_literal(envelope) for envelope in batch)
            dbx.sql(f"INSERT INTO {scratch_table} VALUES\n{values}")
            print(f"inserted {start + len(batch)}/{len(envelopes)} rows", flush=True)

        rows = dbx.sql(
            f"SELECT entity_type, COUNT(*), COUNT(DISTINCT entity_id) FROM {scratch_table} "
            f"WHERE ns = '{args.ns}' GROUP BY entity_type ORDER BY entity_type"
        )
        loaded = {row[0]: {"rows": int(row[1]), "distinct_entity_ids": int(row[2])} for row in rows}
        loaded_rows = {entity: counts["rows"] for entity, counts in loaded.items()}
        if (
            not counts_match_expected(loaded_rows, expected)
            or any(counts["rows"] != counts["distinct_entity_ids"] for counts in loaded.values())
        ):
            raise RuntimeError(f"scratch bronze counts do not match the extract manifest: {loaded}")

        dbx.sql(
            f"""
            INSERT INTO {BRONZE_TABLE}
            REPLACE WHERE ns = '{args.ns}'
            SELECT * FROM {scratch_table} WHERE ns = '{args.ns}'
            """
        )
        rows = dbx.sql(
            f"SELECT entity_type, COUNT(*), COUNT(DISTINCT entity_id) FROM {BRONZE_TABLE} "
            f"WHERE ns = '{args.ns}' GROUP BY entity_type ORDER BY entity_type"
        )
        bronze = {row[0]: {"rows": int(row[1]), "distinct_entity_ids": int(row[2])} for row in rows}
        print(json.dumps({"ns": args.ns, "manifest": expected, "bronze": bronze}, indent=2))
        bronze_rows = {entity: counts["rows"] for entity, counts in bronze.items()}
        if (
            not counts_match_expected(bronze_rows, expected)
            or any(counts["rows"] != counts["distinct_entity_ids"] for counts in bronze.values())
        ):
            print("bronze counts do not match the extract manifest", file=sys.stderr)
            return 1
        return 0
    finally:
        dbx.sql(f"DROP TABLE IF EXISTS {scratch_table}")


if __name__ == "__main__":
    sys.exit(main())
