# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg2-binary", "boto3", "tabulate"]
# ///
"""
Validation harness for the legacy seed data.

Re-derives row/item/object counts and checksums directly from the data stores
and asserts they match the manifest at testdata/legacy/manifests/<ns>.json.
Also re-enumerates the planted data-quality anomalies and asserts their counts
match the manifest's planted_anomalies list.

Usage:
    uv run testdata/legacy/validate.py --ns <ns> [--targets postgres,dynamodb,s3]

Exit codes: 0 = all checks PASS, 1 = failures, 2 = config error.
"""

import argparse
import gzip
import sys

import psycopg2
from tabulate import tabulate

from legacy_common import (
    DATA_LAKE_BUCKET,
    DYNAMO_TABLE,
    Checksum,
    aws_client,
    aws_resource,
    load_manifest,
    pg_config,
    schema_name,
    valid_ns,
)

results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, "PASS" if ok else "FAIL", detail))


def expect(manifest: dict, key: str) -> dict | None:
    target = manifest.get("targets", {}).get(key)
    if target is None:
        check(f"manifest target {key}", False, "missing from manifest")
    return target


def anomaly_count(manifest: dict, kind: str, target: str) -> int | None:
    for a in manifest.get("planted_anomalies", []):
        if a.get("kind") == kind and a.get("target") == target:
            return int(a["count"])
    return None


# ── Postgres ──────────────────────────────────────────────────────────────────


def validate_postgres(ns: str, manifest: dict) -> None:
    schema = schema_name(ns)
    conn = psycopg2.connect(**pg_config())
    try:
        with conn.cursor() as cur:
            checks = {
                "documents": "SELECT id::text || '|' || version || '|' || word_count FROM {s}.documents",
                "document_versions": "SELECT document_id::text || '|' || version_number FROM {s}.document_versions",
                "document_snapshots": "SELECT id::text || '|' || document_id::text FROM {s}.document_snapshots",
            }
            for table, sql in checks.items():
                key = f"postgres.{schema}.{table}"
                want = expect(manifest, key)
                if want is None:
                    continue
                cur.execute(sql.format(s=schema))
                ck = Checksum()
                for row in cur:
                    ck.add(row[0])
                check(f"{key} rows", ck.count == want["rows"],
                      f"store={ck.count} manifest={want['rows']}")
                got = ck.hexdigest()
                check(f"{key} checksum", got == want["checksum"],
                      f"store={got} manifest={want['checksum']}")

            # planted anomaly: documents whose declared version != version rows
            gap_want = anomaly_count(
                manifest, "version_gaps", f"postgres.{schema}.document_versions")
            if gap_want is not None:
                cur.execute(f"""
                    SELECT COUNT(*) FROM {schema}.documents d
                    WHERE d.version != (
                        SELECT COUNT(*) FROM {schema}.document_versions v
                        WHERE v.document_id = d.id)
                """)
                got = cur.fetchone()[0]
                check("anomaly version_gaps", got == gap_want,
                      f"store={got} manifest={gap_want}")

            orphan_want = anomaly_count(
                manifest, "orphaned_snapshots", f"postgres.{schema}.document_snapshots")
            if orphan_want is not None:
                cur.execute(f"""
                    SELECT COUNT(*) FROM {schema}.document_snapshots s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM {schema}.documents d WHERE d.id = s.document_id)
                """)
                got = cur.fetchone()[0]
                check("anomaly orphaned_snapshots", got == orphan_want,
                      f"store={got} manifest={orphan_want}")
    finally:
        conn.close()


# ── DynamoDB ──────────────────────────────────────────────────────────────────


def validate_dynamodb(ns: str, manifest: dict) -> None:
    want = expect(manifest, "dynamodb.file-metadata")
    if want is None:
        return
    table = aws_resource("dynamodb").Table(DYNAMO_TABLE)
    ck, orphans = Checksum(), 0
    scan_kwargs = {
        "ProjectionExpression": "id, size_bytes, s3_key",
        "FilterExpression": "#n = :ns",
        "ExpressionAttributeNames": {"#n": "ns"},
        "ExpressionAttributeValues": {":ns": ns},
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            ck.add(f"{item['id']}|{int(item['size_bytes'])}|{item['s3_key']}")
            if "/missing/" in item["s3_key"]:
                orphans += 1
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    check("dynamodb.file-metadata items", ck.count == want["items"],
          f"store={ck.count} manifest={want['items']}")
    got = ck.hexdigest()
    check("dynamodb.file-metadata checksum", got == want["checksum"],
          f"store={got} manifest={want['checksum']}")

    orphan_want = anomaly_count(manifest, "orphaned_metadata", "dynamodb.file-metadata")
    if orphan_want is not None:
        check("anomaly orphaned_metadata", orphans == orphan_want,
              f"store={orphans} manifest={orphan_want}")


# ── S3 ────────────────────────────────────────────────────────────────────────


def validate_s3(ns: str, manifest: dict) -> None:
    prefix = f"events/{ns}/"
    key = f"s3.data-lake/{prefix}"
    want = expect(manifest, key)
    if want is None:
        return
    s3 = aws_client("s3")
    ck = Checksum()
    total_bytes, n_objects = 0, 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=DATA_LAKE_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=DATA_LAKE_BUCKET, Key=obj["Key"])["Body"].read()
            n_events = gzip.decompress(body).decode().count("\n")
            ck.add(f"{obj['Key']}|{n_events}|{len(body)}")
            total_bytes += len(body)
            n_objects += 1

    check(f"{key} objects", n_objects == want["objects"],
          f"store={n_objects} manifest={want['objects']}")
    check(f"{key} bytes", total_bytes == want["bytes"],
          f"store={total_bytes} manifest={want['bytes']}")
    got = ck.hexdigest()
    check(f"{key} checksum", got == want["checksum"],
          f"store={got} manifest={want['checksum']}")

    missing_want = anomaly_count(manifest, "missing_hours", key)
    days = manifest.get("seed_legacy_params", {}).get("s3", {}).get("event_days")
    if missing_want is not None and days is not None:
        expected_hours = days * 24
        gaps = expected_hours - n_objects
        check("anomaly missing_hours", gaps == missing_want,
              f"store={gaps} manifest={missing_want}")


# ── Main ──────────────────────────────────────────────────────────────────────

VALIDATORS = {
    "postgres": validate_postgres,
    "dynamodb": validate_dynamodb,
    "s3": validate_s3,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--targets", default="postgres,dynamodb,s3")
    args = parser.parse_args()

    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    manifest = load_manifest(args.ns)
    if not manifest:
        print(f"No manifest found for ns '{args.ns}' — run seed-legacy first",
              file=sys.stderr)
        return 2

    requested = [t.strip() for t in args.targets.split(",") if t.strip()]
    unknown = [t for t in requested if t not in VALIDATORS]
    if unknown:
        print(f"Unknown targets: {unknown} (valid: {sorted(VALIDATORS)})",
              file=sys.stderr)
        return 2

    for name in requested:
        VALIDATORS[name](args.ns, manifest)

    print(tabulate(results, headers=["check", "status", "detail"], tablefmt="github"))
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
