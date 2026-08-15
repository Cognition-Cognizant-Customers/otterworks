"""Transformer: pure legacy-row -> Atlas-document mapping (no I/O, no clock).

Rules that the recon depends on:
  - `declaredVersion` is copied verbatim from `documents.version` and is never
    recomputed; `versionCount` is the number of version rows actually present.
    A planted version gap is exactly `declaredVersion != versionCount`, and it
    is reported in `versionGap`, never repaired.
  - Snapshots are not embedded: each document carries `snapshotIds` and the
    blobs live in their own collection. A snapshot whose document is missing is
    quarantined, never dropped.
  - `folderId` is omitted entirely when the source `folder_id` is NULL.
"""

from datetime import datetime, timezone

QUARANTINE_MISSING_DOCUMENT = "missing_document"


def _utc(value: datetime | None) -> datetime | None:
    """Normalize a timestamp to an aware UTC datetime (BSON date)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def migration_meta(ns: str, source_table: str, migrated_at: datetime) -> dict:
    return {"ns": ns, "sourceTable": source_table, "migratedAt": _utc(migrated_at)}


def transform_version(row: dict) -> dict:
    return {
        "versionId": str(row["id"]),
        "versionNumber": int(row["version_number"]),
        "title": row["title"],
        "content": row["content"],
        "createdBy": str(row["created_by"]),
        "createdAt": _utc(row["created_at"]),
    }


def version_gap(declared_version: int, present_numbers: list[int]) -> dict | None:
    """Describe the missing versions of a document, or None when consistent.

    A gap is declared exactly when the source's declared version count differs
    from the number of version rows present; `missing` enumerates the version
    numbers in 1..declaredVersion that have no row.
    """
    present = sorted(set(present_numbers))
    if declared_version == len(present):
        return None
    missing = [v for v in range(1, declared_version + 1) if v not in set(present)]
    return {"missing": missing, "expected": declared_version, "present": len(present)}


def transform_document(
    doc: dict,
    versions: list[dict],
    snapshots: list[dict],
    ns: str,
    source_table: str,
    migrated_at: datetime,
) -> dict:
    embedded = sorted(
        (transform_version(v) for v in versions), key=lambda v: v["versionNumber"]
    )
    declared_version = int(doc["version"])
    out = {
        "_id": str(doc["id"]),
        "title": doc["title"],
        "contentType": doc["content_type"],
        "content": doc["content"],
        "ownerId": str(doc["owner_id"]),
        "isDeleted": bool(doc["is_deleted"]),
        "isTemplate": bool(doc["is_template"]),
        "wordCount": int(doc["word_count"]),
        "declaredVersion": declared_version,
        "versionCount": len(embedded),
        "versions": embedded,
        "snapshotIds": sorted(str(s["id"]) for s in snapshots),
        "createdAt": _utc(doc["created_at"]),
        "updatedAt": _utc(doc["updated_at"]),
        "_migration": migration_meta(ns, source_table, migrated_at),
    }
    if doc.get("folder_id") is not None:
        out["folderId"] = str(doc["folder_id"])
    gap = version_gap(declared_version, [v["versionNumber"] for v in embedded])
    if gap is not None:
        out["versionGap"] = gap
    return out


def transform_snapshot(
    snap: dict,
    ns: str,
    source_table: str,
    migrated_at: datetime,
    quarantine_reason: str | None = None,
) -> dict:
    out = {
        "_id": str(snap["id"]),
        "documentId": str(snap["document_id"]),
        "stateB64": snap["state_b64"],
        "label": snap["label"],
        "createdBy": str(snap["created_by"]),
        "createdAt": _utc(snap["created_at"]),
        "_migration": migration_meta(ns, source_table, migrated_at),
    }
    if quarantine_reason is not None:
        out["quarantine_reason"] = quarantine_reason
    return out
