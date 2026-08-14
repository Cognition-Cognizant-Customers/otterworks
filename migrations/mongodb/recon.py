# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg2-binary", "boto3", "pymongo", "tabulate"]
# ///
"""
Reconciliation: source stores (Postgres, DynamoDB) + seed manifest vs the
migrated MongoDB Atlas collections in ow_tp_<ns>.

Checks, per the manifest contract (docs/tech-partnerships/README.md):
  - counts: every manifest target's rows/items vs the Atlas collections
  - checksums: the manifest's order-independent md5-sum checksums re-derived
    from Atlas using the exact seed line formats
  - spot samples: a deterministic sample of documents/files compared
    field-by-field against the live source stores
  - planted anomalies: re-enumerated FROM ATLAS and asserted equal to the
    manifest's planted_anomalies counts (found, never dropped)

Writes a JSON report to migrations/mongodb/reports/<ns>.json and prints a
PASS/FAIL table. Exit codes: 0 = green, 1 = failures, 2 = config error.

Usage:
    uv run migrations/mongodb/recon.py --ns <ns> [--samples 25]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from tabulate import tabulate

from mongo_common import (
    DOCUMENTS_COLLECTION,
    DYNAMO_TABLE,
    FILES_COLLECTION,
    SNAPSHOTS_COLLECTION,
    Checksum,
    aws_resource,
    db_name,
    load_manifest,
    mongo_client,
    pg_config,
    rng_for,
    schema_name,
    valid_ns,
)

results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, "PASS" if ok else "FAIL", detail))


def manifest_target(manifest: dict, key: str) -> dict | None:
    t = manifest.get("targets", {}).get(key)
    if t is None:
        check(f"manifest target {key}", False, "missing from manifest")
    return t


def anomaly_count(manifest: dict, kind: str, target: str) -> int | None:
    for a in manifest.get("planted_anomalies", []):
        if a.get("kind") == kind and a.get("target") == target:
            return int(a["count"])
    return None


# ── Counts + checksums re-derived from Atlas ─────────────────────────────────


def recon_documents(ns: str, db, manifest: dict) -> None:
    schema = schema_name(ns)
    doc_ck, ver_ck, snap_ck = Checksum(), Checksum(), Checksum()
    gap_docs = 0

    for d in db[DOCUMENTS_COLLECTION].find(
            {}, {"version": 1, "word_count": 1, "versions.version": 1},
            batch_size=1000):
        doc_ck.add(f"{d['_id']}|{d['version']}|{d['word_count']}")
        for v in d["versions"]:
            ver_ck.add(f"{d['_id']}|{v['version']}")
        if d["version"] != len(d["versions"]):
            gap_docs += 1

    for s in db[SNAPSHOTS_COLLECTION].find(
            {}, {"document_id": 1}, batch_size=1000):
        snap_ck.add(f"{s['_id']}|{s['document_id']}")

    for table, ck in (("documents", doc_ck),
                      ("document_versions", ver_ck),
                      ("document_snapshots", snap_ck)):
        key = f"postgres.{schema}.{table}"
        want = manifest_target(manifest, key)
        if want is None:
            continue
        check(f"{key} -> atlas count", ck.count == want["rows"],
              f"atlas={ck.count} manifest={want['rows']}")
        got = ck.hexdigest()
        check(f"{key} -> atlas checksum", got == want["checksum"],
              f"atlas={got} manifest={want['checksum']}")

    gap_want = anomaly_count(
        manifest, "version_gaps", f"postgres.{schema}.document_versions")
    if gap_want is not None:
        check("anomaly version_gaps (atlas)", gap_docs == gap_want,
              f"atlas={gap_docs} manifest={gap_want}")

    orphan_want = anomaly_count(
        manifest, "orphaned_snapshots", f"postgres.{schema}.document_snapshots")
    if orphan_want is not None:
        orphans = next(db[SNAPSHOTS_COLLECTION].aggregate([
            {"$lookup": {"from": DOCUMENTS_COLLECTION, "localField": "document_id",
                         "foreignField": "_id", "as": "doc"}},
            {"$match": {"doc": {"$size": 0}}},
            {"$count": "n"},
        ]), {"n": 0})["n"]
        check("anomaly orphaned_snapshots (atlas)", orphans == orphan_want,
              f"atlas={orphans} manifest={orphan_want}")


def recon_files(ns: str, db, manifest: dict) -> None:
    want = manifest_target(manifest, "dynamodb.file-metadata")
    if want is None:
        return
    ck, orphans = Checksum(), 0
    for f in db[FILES_COLLECTION].find(
            {}, {"size_bytes": 1, "s3_key": 1}, batch_size=1000):
        ck.add(f"{f['_id']}|{f['size_bytes']}|{f['s3_key']}")
        if "/missing/" in f["s3_key"]:
            orphans += 1
    check("dynamodb.file-metadata -> atlas count", ck.count == want["items"],
          f"atlas={ck.count} manifest={want['items']}")
    got = ck.hexdigest()
    check("dynamodb.file-metadata -> atlas checksum", got == want["checksum"],
          f"atlas={got} manifest={want['checksum']}")

    orphan_want = anomaly_count(manifest, "orphaned_metadata", "dynamodb.file-metadata")
    if orphan_want is not None:
        check("anomaly orphaned_metadata (atlas)", orphans == orphan_want,
              f"atlas={orphans} manifest={orphan_want}")


# ── Field-level spot samples against the live sources ────────────────────────

DOC_FIELDS = ("title", "content", "content_type", "owner_id", "folder_id",
              "is_deleted", "is_template", "word_count", "version",
              "created_at", "updated_at")
FILE_FIELDS = ("name", "mime_type", "size_bytes", "s3_key", "folder_id",
               "owner_id", "version", "is_trashed", "created_at", "updated_at")


def utc(v):
    if isinstance(v, datetime):
        return (v.astimezone(timezone.utc) if v.tzinfo
                else v.replace(tzinfo=timezone.utc))
    return v


def sample_documents(ns: str, db, n: int) -> None:
    schema = schema_name(ns)
    rng = rng_for(ns, "recon-sample-docs")
    conn = psycopg2.connect(**pg_config())
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {schema}.documents")
            total = cur.fetchone()[0]
            offsets = rng.sample(range(total), min(n, total))
            picks = []
            for off in offsets:
                cur.execute(
                    f"SELECT id::text FROM {schema}.documents "
                    "ORDER BY id OFFSET %s LIMIT 1", (off,))
                picks.append(cur.fetchone()[0])
            mismatches = []
            for doc_id in picks:
                cur.execute(f"""
                    SELECT title, content, content_type, owner_id::text,
                           folder_id::text, is_deleted, is_template,
                           word_count, version, created_at, updated_at,
                           (SELECT COUNT(*) FROM {schema}.document_versions v
                             WHERE v.document_id = d.id)
                      FROM {schema}.documents d WHERE id = %s
                """, (doc_id,))
                row = cur.fetchone()
                src = dict(zip(DOC_FIELDS + ("version_rows",), row))
                got = db[DOCUMENTS_COLLECTION].find_one({"_id": doc_id})
                if got is None:
                    mismatches.append(f"{doc_id}: missing in atlas")
                    continue
                for f in DOC_FIELDS:
                    if utc(src[f]) != utc(got[f]):
                        mismatches.append(f"{doc_id}.{f}: pg={src[f]!r} atlas={got[f]!r}")
                if src["version_rows"] != len(got["versions"]):
                    mismatches.append(
                        f"{doc_id}.versions: pg={src['version_rows']} "
                        f"atlas={len(got['versions'])}")
    finally:
        conn.close()
    check(f"spot-sample documents ({len(picks)})", not mismatches,
          "; ".join(mismatches[:5]) or "all fields equal")


def sample_files(ns: str, db, n: int) -> None:
    rng = rng_for(ns, "recon-sample-files")
    total = db[FILES_COLLECTION].count_documents({})
    offsets = sorted(rng.sample(range(total), min(n, total)))
    picks = []
    cursor = db[FILES_COLLECTION].find({}, {"_id": 1}).sort("_id", 1)
    want = iter(offsets)
    nxt = next(want, None)
    for i, f in enumerate(cursor):
        if nxt is None:
            break
        if i == nxt:
            picks.append(f["_id"])
            nxt = next(want, None)
    table = aws_resource("dynamodb").Table(DYNAMO_TABLE)
    mismatches = []
    for item_id in picks:
        src = table.get_item(Key={"id": item_id}).get("Item")
        got = db[FILES_COLLECTION].find_one({"_id": item_id})
        if src is None or src.get("ns") != ns:
            mismatches.append(f"{item_id}: missing in dynamodb slice")
            continue
        for f in FILE_FIELDS:
            s, g = src[f], got[f]
            if f in ("size_bytes", "version"):
                s = int(s)
            if f in ("created_at", "updated_at"):
                g = utc(g).strftime("%Y-%m-%dT%H:%M:%SZ")
            if s != g:
                mismatches.append(f"{item_id}.{f}: dynamo={s!r} atlas={g!r}")
    check(f"spot-sample files ({len(picks)})", not mismatches,
          "; ".join(mismatches[:5]) or "all fields equal")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--samples", type=int, default=25)
    args = parser.parse_args()

    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    manifest = load_manifest(args.ns)
    if not manifest:
        print(f"No manifest for ns '{args.ns}' — run seed-legacy first",
              file=sys.stderr)
        return 2

    client = mongo_client()
    db = client[db_name(args.ns)]

    recon_documents(args.ns, db, manifest)
    recon_files(args.ns, db, manifest)
    sample_documents(args.ns, db, args.samples)
    sample_files(args.ns, db, args.samples)
    client.close()

    print(tabulate(results, headers=["check", "status", "detail"],
                   tablefmt="github"))
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")

    reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"{args.ns}.json"
    report_path.write_text(json.dumps({
        "namespace": args.ns,
        "database": db_name(args.ns),
        "manifest_seed": manifest.get("seed"),
        "checks": [{"check": c, "status": s, "detail": d} for c, s, d in results],
        "passed": len(results) - len(failed),
        "failed": len(failed),
    }, indent=2) + "\n")
    print(f"report written: {report_path.relative_to(Path.cwd())}"
          if report_path.is_relative_to(Path.cwd()) else f"report written: {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
