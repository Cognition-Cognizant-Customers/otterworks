# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3==1.35.36", "pymongo==4.10.1", "pytest==8.3.3"]
# ///
"""Unit tests for the `mongo_files` item-to-document mapping.

These cover the branches the seeded ns=demo estate does not exercise (binary
attributes, NULL attribution, unexpected extra attributes) so the contract's
malformed-record and encoding policies are provable without live data:

    make tp-mongo-test
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bson.binary import Binary

sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_files import quarantine_id, transform  # noqa: E402
from mongo_common import bson_value  # noqa: E402
from recon_files import typed_correctly  # noqa: E402

ITEM = {
    "id": "0af5ccb8-7313-4567-b3d8-03c22efbc3a2",
    "ns": "demo",
    "name": "file-demo-0004350.zip",
    "mime_type": "application/zip",
    "size_bytes": Decimal("166995131"),
    "s3_key": "demo/files/owner/0af5ccb8-7313-4567-b3d8-03c22efbc3a2",
    "folder_id": "6503e4b3-387e-469c-b644-f4d0341bae63",
    "owner_id": "0bfccc9c-ac37-4c25-903f-c18b196dab0b",
    "version": Decimal("9"),
    "is_trashed": False,
    "created_at": "2024-12-26T05:14:21Z",
    "updated_at": "2024-12-30T15:14:21Z",
}


def item(**overrides) -> dict:
    merged = dict(ITEM)
    for key, value in overrides.items():
        if value is ...:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def test_numeric_attributes_become_bson_numbers_not_strings():
    doc, bad = transform(item())
    assert bad is None
    assert doc["size_bytes"] == 166995131 and isinstance(doc["size_bytes"], int)
    assert doc["version"] == 9 and isinstance(doc["version"], int)
    assert doc["is_trashed"] is False
    assert doc["created_at"] == datetime(2024, 12, 26, 5, 14, 21, tzinfo=timezone.utc)


def test_tenant_field_carries_the_ns_attribute():
    doc, _ = transform(item())
    assert doc["tenant"] == "demo"
    assert doc["_id"] == ITEM["id"]


def test_binary_attribute_stays_bson_binary():
    doc, bad = transform(item(thumbnail=b"\x89PNG\x00\xff"))
    assert bad is None
    assert doc["extras"]["thumbnail"] == Binary(b"\x89PNG\x00\xff")
    assert isinstance(doc["extras"]["thumbnail"], bytes)


def test_unexpected_attributes_are_carried_under_extras():
    doc, _ = transform(item(legacy_flag="Y", retention_days=Decimal("30")))
    assert doc["extras"] == {"legacy_flag": "Y", "retention_days": 30}
    assert "legacy_flag" not in doc


def test_orphaned_s3_key_is_marked_not_dropped():
    doc, _ = transform(item(s3_key="demo/missing/owner/abc"))
    assert doc["s3_object_missing"] is True
    doc, _ = transform(item())
    assert doc["s3_object_missing"] is False


def test_missing_required_attribute_is_quarantined():
    for attr in ("id", "ns", "s3_key", "size_bytes"):
        doc, bad = transform(item(**{attr: ...}))
        assert doc is None
        assert bad["reason"] == f"missing_required_attribute:{attr}"


def test_null_required_value_never_fails_open():
    doc, bad = transform(item(size_bytes=None))
    assert doc is None
    assert bad["reason"] == "null_required_attribute:size_bytes"
    doc, bad = transform(item(s3_key=""))
    assert doc is None
    assert bad["reason"] == "null_required_attribute:s3_key"


def test_null_optional_value_is_quarantined_rather_than_defaulted():
    doc, bad = transform(item(name=None))
    assert doc is None
    assert bad["reason"] == "null_attribute:name"


def test_unparseable_timestamp_is_quarantined():
    doc, bad = transform(item(created_at="31-FEB-24"))
    assert doc is None
    assert bad["reason"].startswith("unparseable_created_at")


def test_quarantine_record_keeps_raw_bytes_as_hex():
    _, bad = transform(item(size_bytes=None, payload=b"\xff\xfe"))
    assert bad["raw_item"]["payload"] == {"hex": "fffe"}


def test_wrongly_typed_required_value_is_quarantined_not_crashed():
    for attr, bad in (("s3_key", Decimal("7")), ("id", Decimal("7")),
                      ("ns", b"demo"), ("size_bytes", "166995131")):
        doc, record = transform(item(**{attr: bad}))
        assert doc is None
        assert record["reason"] == f"wrongly_typed_required_attribute:{attr}"


def test_wrongly_typed_optional_value_is_quarantined_not_left_to_the_validator():
    """Anything the target validator constrains is attributed before the write."""
    for attr, bad in (("name", Decimal("7")), ("owner_id", Decimal("7")),
                      ("version", "9"), ("is_trashed", Decimal("1"))):
        doc, record = transform(item(**{attr: bad}))
        assert doc is None
        assert record["reason"] == f"wrongly_typed_attribute:{attr}"


def test_out_of_range_value_is_quarantined_not_left_to_the_validator():
    for attr, bad in (("size_bytes", Decimal("-1")), ("version", Decimal("0"))):
        doc, record = transform(item(**{attr: bad}))
        assert doc is None
        assert record["reason"] == f"out_of_range_attribute:{attr}"


def test_non_integral_or_oversized_number_is_quarantined_not_written_as_a_double():
    """DynamoDB N values are Decimals; only int64 integers satisfy the validator."""
    doc, record = transform(item(size_bytes=Decimal("1.5")))
    assert doc is None
    assert record["reason"] == "non_integral_attribute:size_bytes"
    doc, record = transform(item(version=Decimal("2" + "0" * 19)))
    assert doc is None
    assert record["reason"] == "out_of_range_attribute:version"


def test_absent_optional_attributes_still_migrate():
    doc, bad = transform(item(version=..., is_trashed=..., created_at=..., name=...))
    assert bad is None
    assert set(doc) == {"_id", "tenant", "mime_type", "size_bytes", "s3_key",
                        "folder_id", "owner_id", "updated_at", "s3_object_missing"}


def test_distinct_malformed_items_never_share_a_quarantine_id():
    """Two items rejected for the same reason with no usable key must both survive."""
    _, first = transform(item(id=..., name="a.zip"))
    _, second = transform(item(id=..., name="b.zip"))
    assert first["source_key"] is None and second["source_key"] is None
    assert quarantine_id(first) != quarantine_id(second)
    assert quarantine_id(first) == quarantine_id(transform(item(id=..., name="a.zip"))[1])


def test_recon_type_check_accepts_a_document_that_omits_optional_attributes():
    """A sparse item is a correct migration, not a BSON-type failure."""
    doc, _ = transform(item(version=..., is_trashed=..., created_at=..., updated_at=...))
    assert typed_correctly(doc)
    assert not typed_correctly({**doc, "size_bytes": "166995131"})
    assert not typed_correctly({**doc, "version": True})


def test_bson_value_keeps_fractional_numbers_as_floats():
    assert bson_value(Decimal("1.5")) == 1.5
    assert bson_value({"a": [Decimal("2"), b"x"]}) == {"a": [2, Binary(b"x")]}
