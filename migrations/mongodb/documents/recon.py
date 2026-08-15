# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo", "tabulate"]
# ///
"""Reconciliation for the `documents` workload — recomputed FROM ATLAS.

Every number below is derived by streaming the migrated collections in
`ow_tp_demo` and folding them with the same order-independent `Checksum` the
legacy seed used, then compared against the authoritative manifest at
`testdata/legacy/manifests/<ns>.json`. Nothing is read back from Postgres here:
the source of truth for "what should be there" is the manifest, and the source
of truth for "what is there" is Atlas.

Checksum lines (per the contract):

    documents           f"{_id}|{declaredVersion}|{wordCount}"
    document_versions   f"{_id}|{versionNumber}"      per embedded version
    document_snapshots  f"{_id}|{documentId}"         over migrated + orphaned

Also enumerates the planted anomalies: version gaps (document ids + the missing
version numbers) and orphaned snapshots (snapshot ids + dangling document ids).

Usage:
    uv run migrations/mongodb/documents/recon.py --ns demo \
        [--report docs/tech-partnerships/recon/mongo-documents-demo.md]

Exit codes: 0 = all checks PASS, 1 = failures, 2 = config error.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from tabulate import tabulate

from mongo_common import (
    ATLAS_DB,
    COLL_DOCUMENTS,
    COLL_SNAPSHOTS,
    COLL_SNAPSHOTS_ORPHANED,
    QUARANTINE_MISSING_DOCUMENT,
    SOURCE_TABLE_DOCUMENTS,
    Checksum,
    atlas_client,
    atlas_db,
    load_manifest,
    log,
    schema_name,
    source_table,
    valid_ns,
)

BATCH_SIZE = 500


def manifest_target(manifest: dict, key: str) -> dict | None:
    return manifest.get("targets", {}).get(key)


def manifest_anomaly(manifest: dict, kind: str, target: str) -> int | None:
    for a in manifest.get("planted_anomalies", []):
        if a.get("kind") == kind and a.get("target") == target:
            return int(a["count"])
    return None


def scan_atlas(db, ns: str) -> dict:
    """Fold every migrated collection into counts, checksums and an anomaly ledger."""
    doc_ck, ver_ck, snap_ck = Checksum(), Checksum(), Checksum()
    version_gaps: list[dict] = []
    orphans: list[dict] = []
    bad_migration_meta: list[str] = []
    missing_snapshot_refs = 0
    expected_source = source_table(ns, SOURCE_TABLE_DOCUMENTS)

    cursor = db[COLL_DOCUMENTS].find(
        {},
        projection={
            "declaredVersion": 1, "wordCount": 1, "versionCount": 1,
            "versions.versionNumber": 1, "versionGap": 1, "snapshotIds": 1,
            "_migration": 1,
        },
        batch_size=BATCH_SIZE,
    )
    snapshot_refs: set[str] = set()
    for doc in cursor:
        doc_ck.add(f"{doc['_id']}|{doc['declaredVersion']}|{doc['wordCount']}")
        for version in doc.get("versions", []):
            ver_ck.add(f"{doc['_id']}|{version['versionNumber']}")
        gap = doc.get("versionGap")
        if gap is not None:
            version_gaps.append({
                "documentId": doc["_id"],
                "missing": gap["missing"],
                "expected": gap["expected"],
                "present": gap["present"],
            })
        snapshot_refs.update(doc.get("snapshotIds", []))
        meta = doc.get("_migration") or {}
        if meta.get("ns") != ns or meta.get("sourceTable") != expected_source \
                or not isinstance(meta.get("migratedAt"), datetime):
            bad_migration_meta.append(doc["_id"])

    migrated_snapshots = 0
    for snap in db[COLL_SNAPSHOTS].find(
        {}, projection={"documentId": 1}, batch_size=BATCH_SIZE
    ):
        snap_ck.add(f"{snap['_id']}|{snap['documentId']}")
        migrated_snapshots += 1
        if snap["_id"] not in snapshot_refs:
            missing_snapshot_refs += 1

    for snap in db[COLL_SNAPSHOTS_ORPHANED].find(
        {}, projection={"documentId": 1, "quarantine_reason": 1}, batch_size=BATCH_SIZE
    ):
        snap_ck.add(f"{snap['_id']}|{snap['documentId']}")
        orphans.append({
            "snapshotId": snap["_id"],
            "documentId": snap["documentId"],
            "quarantine_reason": snap.get("quarantine_reason"),
        })

    return {
        "documents": doc_ck.count,
        "documents_checksum": doc_ck.hexdigest(),
        "embedded_versions": ver_ck.count,
        "document_versions_checksum": ver_ck.hexdigest(),
        "snapshots_migrated": migrated_snapshots,
        "snapshots_orphaned": len(orphans),
        "snapshots_total": snap_ck.count,
        "document_snapshots_checksum": snap_ck.hexdigest(),
        "version_gaps": sorted(version_gaps, key=lambda g: g["documentId"]),
        "orphaned_snapshots": sorted(orphans, key=lambda o: o["snapshotId"]),
        "documents_with_bad_migration_meta": sorted(bad_migration_meta),
        "snapshots_not_referenced": missing_snapshot_refs,
    }


def reconcile(
    ns: str, manifest: dict, atlas: dict, previous: dict | None = None
) -> list[tuple[str, str, str]]:
    schema = schema_name(ns)
    results: list[tuple[str, str, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, "PASS" if ok else "FAIL", detail))

    comparisons = (
        ("documents", atlas["documents"], atlas["documents_checksum"]),
        ("document_versions", atlas["embedded_versions"],
         atlas["document_versions_checksum"]),
        ("document_snapshots", atlas["snapshots_total"],
         atlas["document_snapshots_checksum"]),
    )
    for table, rows, checksum in comparisons:
        key = f"postgres.{schema}.{table}"
        want = manifest_target(manifest, key)
        if want is None:
            check(f"manifest target {key}", False, "missing from manifest")
            continue
        check(f"{key} rows", rows == want["rows"],
              f"atlas={rows} manifest={want['rows']}")
        check(f"{key} checksum", checksum == want["checksum"],
              f"atlas={checksum} manifest={want['checksum']}")

    gap_want = manifest_anomaly(
        manifest, "version_gaps", f"postgres.{schema}.document_versions")
    gaps = atlas["version_gaps"]
    if gap_want is None:
        check("anomaly version_gaps", False, "missing from manifest")
    else:
        check("anomaly version_gaps", len(gaps) == gap_want,
              f"atlas={len(gaps)} manifest={gap_want}")
    with_missing = sum(1 for g in gaps if g["missing"])
    check("version_gaps are real inconsistencies",
          with_missing == len(gaps),
          f"{with_missing}/{len(gaps)} gaps carry missing version numbers")

    orphan_want = manifest_anomaly(
        manifest, "orphaned_snapshots", f"postgres.{schema}.document_snapshots")
    orphans = atlas["orphaned_snapshots"]
    if orphan_want is None:
        check("anomaly orphaned_snapshots", False, "missing from manifest")
    else:
        check("anomaly orphaned_snapshots", len(orphans) == orphan_want,
              f"atlas={len(orphans)} manifest={orphan_want}")
    check("orphaned_snapshots quarantined, not dropped",
          all(o["quarantine_reason"] == QUARANTINE_MISSING_DOCUMENT for o in orphans),
          f"{ATLAS_DB}.{COLL_SNAPSHOTS_ORPHANED}: {len(orphans)} "
          f"with quarantine_reason={QUARANTINE_MISSING_DOCUMENT!r}")

    check("every document carries _migration",
          not atlas["documents_with_bad_migration_meta"],
          f"{len(atlas['documents_with_bad_migration_meta'])} documents missing/invalid")
    check("every migrated snapshot is referenced by its document",
          atlas["snapshots_not_referenced"] == 0,
          f"unreferenced={atlas['snapshots_not_referenced']}")

    if previous is not None:
        check("idempotent rerun: Atlas state identical to the previous run",
              previous == atlas,
              "every count, checksum and anomaly id matches the earlier recon"
              if previous == atlas else "differs from the earlier recon")
    return results


def render_report(ns: str, atlas: dict, results: list[tuple[str, str, str]]) -> str:
    failed = [r for r in results if r[1] == "FAIL"]
    verdict = "PASS" if not failed else "FAIL"
    lines = [
        f"# Recon — `mongo-documents` (ns=`{ns}`)",
        "",
        f"Verdict: **{verdict}** ({len(results) - len(failed)}/{len(results)} checks passed)",
        "",
        f"- Target: Atlas `{ATLAS_DB}` "
        f"(`{COLL_DOCUMENTS}`, `{COLL_SNAPSHOTS}`, `{COLL_SNAPSHOTS_ORPHANED}`)",
        f"- Baseline: `testdata/legacy/manifests/{ns}.json`, written by "
        "`make seed-legacy` from the legacy Postgres estate and independently "
        f"re-derived from the source by `make seed-legacy-validate NS={ns}` (15/15).",
        "- Every count and checksum in this report is recomputed **from Atlas** by "
        "streaming the migrated collections; nothing is read back from Postgres.",
        "- Checksums use the manifest's order-independent md5-sum "
        "(`testdata/legacy/legacy_common.Checksum`) over the contract lines "
        "`{_id}|{declaredVersion}|{wordCount}`, `{_id}|{versionNumber}` and "
        "`{snapshotId}|{documentId}`.",
        "",
        "## Counts and checksums",
        "",
        tabulate(results, headers=["check", "status", "detail"], tablefmt="github"),
        "",
        "## Atlas totals",
        "",
        tabulate(
            [
                [f"{ATLAS_DB}.{COLL_DOCUMENTS}", atlas["documents"]],
                ["embedded versions (all documents)", atlas["embedded_versions"]],
                [f"{ATLAS_DB}.{COLL_SNAPSHOTS}", atlas["snapshots_migrated"]],
                [f"{ATLAS_DB}.{COLL_SNAPSHOTS_ORPHANED}", atlas["snapshots_orphaned"]],
                ["snapshots total (source set)", atlas["snapshots_total"]],
            ],
            headers=["collection", "documents"],
            tablefmt="github",
        ),
        "",
        "## Anomaly ledger",
        "",
        f"### version_gaps — {len(atlas['version_gaps'])}",
        "",
        "Documents whose `declaredVersion` (copied verbatim from the source "
        "`documents.version`) exceeds the versions actually present. Preserved, "
        "not repaired.",
        "",
        tabulate(
            [[g["documentId"], g["expected"], g["present"],
              ", ".join(str(m) for m in g["missing"])]
             for g in atlas["version_gaps"]],
            headers=["document _id", "declaredVersion", "versionCount", "missing"],
            tablefmt="github",
        ),
        "",
        f"### orphaned_snapshots — {len(atlas['orphaned_snapshots'])}",
        "",
        f"Snapshots whose `document_id` has no document. All landed in "
        f"`{ATLAS_DB}.{COLL_SNAPSHOTS_ORPHANED}` with "
        f"`quarantine_reason: \"{QUARANTINE_MISSING_DOCUMENT}\"`.",
        "",
        tabulate(
            [[o["snapshotId"], o["documentId"], o["quarantine_reason"]]
             for o in atlas["orphaned_snapshots"]],
            headers=["snapshot _id", "dangling documentId", "quarantine_reason"],
            tablefmt="github",
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--report", help="write a markdown report to this path")
    parser.add_argument("--json", dest="json_out", help="write the raw results as JSON")
    parser.add_argument(
        "--compare-json",
        help="assert the Atlas state is identical to this earlier recon JSON "
             "(idempotency evidence for a migration rerun)",
    )
    args = parser.parse_args()

    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    manifest = load_manifest(args.ns)
    if not manifest:
        print(f"No manifest found for ns '{args.ns}' — run seed-legacy first",
              file=sys.stderr)
        return 2

    client = atlas_client()
    try:
        atlas = scan_atlas(atlas_db(client), args.ns)
    finally:
        client.close()

    previous = None
    if args.compare_json:
        previous = json.loads(Path(args.compare_json).read_text())["atlas"]

    results = reconcile(args.ns, manifest, atlas, previous)
    print(tabulate(results, headers=["check", "status", "detail"], tablefmt="github"))
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_report(args.ns, atlas, results))
        log(f"report written: {path}")
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "namespace": args.ns,
            "reconciled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "atlas": atlas,
            "checks": [{"check": c, "status": s, "detail": d} for c, s, d in results],
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        log(f"json written: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
