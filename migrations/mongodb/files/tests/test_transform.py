from datetime import datetime, timezone
from decimal import Decimal

import pytest
from bson import Int64
from transform import (
    ORPHAN_REASON,
    is_orphan_key,
    parse_timestamp,
    to_int64,
    transform_item,
)

MIGRATED_AT = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def item(**overrides) -> dict:
    base = {
        "id": "6f0a5b6e-1f5a-4c8d-9b2e-2f9a0d1c3b4a",
        "ns": "demo",
        "name": "file-demo-0000001.pdf",
        "mime_type": "application/pdf",
        "size_bytes": Decimal(12345678),
        "s3_key": "demo/files/8c1d/6f0a5b6e-1f5a-4c8d-9b2e-2f9a0d1c3b4a",
        "folder_id": "b4d0d4f6-0000-4000-8000-000000000001",
        "owner_id": "8c1d",
        "version": Decimal(3),
        "is_trashed": False,
        "created_at": "2026-07-01T10:11:12Z",
        "updated_at": "2026-07-15T00:00:00Z",
    }
    return {**base, **overrides}


def test_healthy_item_maps_every_field():
    result = transform_item(item(), "demo", MIGRATED_AT)
    document = result.document

    assert result.orphan is False
    assert result.unparsed_timestamps == []
    assert document == {
        "_id": "6f0a5b6e-1f5a-4c8d-9b2e-2f9a0d1c3b4a",
        "tenant": "demo",
        "name": "file-demo-0000001.pdf",
        "mimeType": "application/pdf",
        "sizeBytes": Int64(12345678),
        "storage": {
            "s3Key": "demo/files/8c1d/6f0a5b6e-1f5a-4c8d-9b2e-2f9a0d1c3b4a",
            "present": True,
        },
        "folderId": "b4d0d4f6-0000-4000-8000-000000000001",
        "ownerId": "8c1d",
        "version": 3,
        "isTrashed": False,
        "createdAt": datetime(2026, 7, 1, 10, 11, 12, tzinfo=timezone.utc),
        "updatedAt": datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc),
        "_migration": {
            "ns": "demo",
            "sourceTable": "dynamodb:otterworks-file-metadata",
            "migratedAt": MIGRATED_AT,
        },
    }


def test_size_bytes_is_bson_int64_not_float():
    document = transform_item(item(size_bytes=Decimal(249999999)), "demo", MIGRATED_AT).document

    assert isinstance(document["sizeBytes"], Int64)
    assert not isinstance(document["sizeBytes"], float)
    assert document["sizeBytes"] == 249999999
    # The recon checksum renders the value as a plain integer.
    assert f"{document['sizeBytes']}" == "249999999"


def test_size_bytes_beyond_int32_survives():
    document = transform_item(item(size_bytes=Decimal(4294967296)), "demo", MIGRATED_AT).document

    assert isinstance(document["sizeBytes"], Int64)
    assert int(document["sizeBytes"]) == 4294967296


@pytest.mark.parametrize("value", [Decimal(128), 128, "128"])
def test_to_int64_accepts_integral_representations(value):
    assert to_int64(value, "size_bytes") == Int64(128)


@pytest.mark.parametrize("value", [Decimal("1.5"), 1.5, "1.5", None, True])
def test_to_int64_rejects_non_integers(value):
    with pytest.raises(ValueError):
        to_int64(value, "size_bytes")


def test_orphan_marker_is_flagged_in_place():
    orphan_key = "demo/missing/8c1d/6f0a5b6e-1f5a-4c8d-9b2e-2f9a0d1c3b4a"
    result = transform_item(item(s3_key=orphan_key), "demo", MIGRATED_AT)

    assert result.orphan is True
    # Flag-in-place: the document is still migrated, with the key preserved.
    assert result.document["storage"] == {
        "s3Key": orphan_key,
        "present": False,
        "orphanReason": ORPHAN_REASON,
    }
    assert result.document["_id"] == item()["id"]


@pytest.mark.parametrize(
    ("s3_key", "expected"),
    [
        ("demo/files/o/u", False),
        ("demo/missing/o/u", True),
        # Only this namespace's marker counts, and only as a path segment.
        ("t01/missing/o/u", False),
        ("demo/missingno/o/u", False),
        ("demo/files/o/missing/u", False),
    ],
)
def test_is_orphan_key(s3_key, expected):
    assert is_orphan_key(s3_key, "demo") is expected


def test_unparseable_timestamp_is_kept_raw_and_reported():
    result = transform_item(item(updated_at="31-FEB-24"), "demo", MIGRATED_AT)

    assert result.unparsed_timestamps == ["updatedAt"]
    assert result.document["updatedAt"] == "31-FEB-24"
    assert isinstance(result.document["createdAt"], datetime)


@pytest.mark.parametrize("value", ["", "N/A", "2026-07-01 10:11:12", None])
def test_parse_timestamp_returns_none_for_bad_values(value):
    assert parse_timestamp(value) is None


def test_parsed_timestamps_are_utc_aware():
    parsed = parse_timestamp("2026-07-01T10:11:12Z")

    assert parsed == datetime(2026, 7, 1, 10, 11, 12, tzinfo=timezone.utc)
    assert parsed.tzinfo is timezone.utc


def test_foreign_namespace_item_is_rejected():
    with pytest.raises(ValueError, match="belongs to ns 't01'"):
        transform_item(item(ns="t01"), "demo", MIGRATED_AT)


def test_transform_is_pure():
    source = item()
    snapshot = dict(source)
    first = transform_item(source, "demo", MIGRATED_AT).document
    second = transform_item(source, "demo", MIGRATED_AT).document

    assert source == snapshot
    assert first == second


def test_trashed_flag_and_version_are_native_types():
    document = transform_item(
        item(is_trashed=True, version=Decimal(9)), "demo", MIGRATED_AT
    ).document

    assert document["isTrashed"] is True
    assert document["version"] == 9
    assert isinstance(document["version"], int)
