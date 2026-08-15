"""Transformer — DynamoDB file-metadata item -> Atlas `files` document.

Pure: no I/O, no clock reads (the caller supplies `migrated_at`), so every case
below is unit-testable. The source is already a document store, so this is a
type-fidelity and tenancy exercise rather than a remodel:

  * `size_bytes` becomes a BSON int64 so it round-trips exactly (a float would
    break the reconciliation checksum),
  * ISO-8601 strings become BSON dates; a value that fails to parse is kept as
    the raw string and reported, never dropped,
  * the `ns` attribute becomes the `tenant` field — the only carrier of the
    namespace in the target document,
  * orphans are flagged in place (`storage.present: false`), never quarantined:
    the signal is the `<ns>/missing/…` key marker, not object existence.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from bson import Int64
from common import SOURCE_TABLE

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MISSING_OBJECT_PREFIX = "missing"
ORPHAN_REASON = "missing_object_marker"
TIMESTAMP_FIELDS = (("created_at", "createdAt"), ("updated_at", "updatedAt"))


@dataclass
class TransformResult:
    document: dict
    orphan: bool = False
    unparsed_timestamps: list[str] = field(default_factory=list)


def parse_timestamp(value: str) -> datetime | None:
    """ISO-8601 UTC string -> tz-aware datetime, or None if unparseable."""
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def to_int64(value: object, field_name: str) -> Int64:
    """Exact integer -> BSON int64; anything fractional is a hard error."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} is a bool, not a number")
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError(f"{field_name} is not an integer: {value}")
        return Int64(int(value))
    if isinstance(value, int):
        return Int64(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return Int64(int(value))
    raise ValueError(f"{field_name} is not an integer: {value!r}")


def is_orphan_key(s3_key: str, ns: str) -> bool:
    """True when the storage key carries the `<ns>/missing/…` orphan marker."""
    return s3_key.startswith(f"{ns}/{MISSING_OBJECT_PREFIX}/")


def transform_item(item: dict, ns: str, migrated_at: datetime) -> TransformResult:
    """Build the `files` document for one source item.

    Raises ValueError for an item from another namespace: the loader must never
    write across the tenancy boundary.
    """
    item_ns = item.get("ns")
    if item_ns != ns:
        raise ValueError(f"item {item.get('id')!r} belongs to ns {item_ns!r}, not {ns!r}")

    s3_key = item["s3_key"]
    orphan = is_orphan_key(s3_key, ns)
    storage: dict = {"s3Key": s3_key, "present": not orphan}
    if orphan:
        storage["orphanReason"] = ORPHAN_REASON

    document = {
        "_id": item["id"],
        "tenant": item_ns,
        "name": item["name"],
        "mimeType": item["mime_type"],
        "sizeBytes": to_int64(item["size_bytes"], "size_bytes"),
        "storage": storage,
        "folderId": item["folder_id"],
        "ownerId": item["owner_id"],
        "version": int(item["version"]),
        "isTrashed": bool(item["is_trashed"]),
        "_migration": {
            "ns": ns,
            "sourceTable": SOURCE_TABLE,
            "migratedAt": migrated_at,
        },
    }

    result = TransformResult(document=document, orphan=orphan)
    for source_field, target_field in TIMESTAMP_FIELDS:
        raw = item[source_field]
        parsed = parse_timestamp(raw)
        if parsed is None:
            document[target_field] = raw
            result.unparsed_timestamps.append(target_field)
        else:
            document[target_field] = parsed
    return result
