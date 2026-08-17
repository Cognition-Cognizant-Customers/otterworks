from datetime import datetime, timedelta, timezone

from documents_model import (
    VERSION_ARRAY_BOUND,
    missing_versions_for,
    process_document,
    utc_datetime,
)


def document_row(**overrides):
    values = [
        "doc-1",
        "Title",
        "Body",
        "text/plain",
        "owner-1",
        None,
        False,
        False,
        3,
        1,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, tzinfo=timezone.utc),
    ]
    fields = (
        "id",
        "title",
        "content",
        "content_type",
        "owner_id",
        "folder_id",
        "is_deleted",
        "is_template",
        "word_count",
        "version",
        "created_at",
        "updated_at",
    )
    values = dict(zip(fields, values))
    values.update(overrides)
    return tuple(values[field] for field in fields)


def test_version_gap_detection_covers_interior_gap():
    assert missing_versions_for(4, [1, 3, 4]) == [2]


def test_version_gap_detection_covers_truncated_tail():
    assert missing_versions_for(5, [1, 2, 3]) == [4, 5]


def test_version_gap_detection_extends_beyond_declared_version():
    assert missing_versions_for(3, [1, 2, 5]) == [3, 4]


def test_version_bound_exceeded_quarantines_without_truncated_document():
    versions = [
        {
            "_id": f"version-{number}",
            "version_number": number,
            "title": "Title",
            "content": "Body",
            "created_by": "owner-1",
            "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        }
        for number in range(1, VERSION_ARRAY_BOUND + 2)
    ]
    document, quarantine, _ = process_document("demo", document_row(), versions)

    assert document is None
    assert quarantine["reason"] == "version_array_over_bound"
    assert str(VERSION_ARRAY_BOUND + 1) in quarantine["detail"]


def test_required_null_field_is_quarantined():
    document, quarantine, _ = process_document(
        "demo", document_row(title=None), []
    )

    assert document is None
    assert quarantine["reason"] == "null_required_field"
    assert "title" in quarantine["detail"]


def test_invalid_utf8_is_quarantined_with_hex_raw_bytes():
    document, quarantine, _ = process_document(
        "demo", document_row(content=b"\xff\x00"), []
    )

    assert document is None
    assert quarantine["reason"] == "invalid_utf8"
    assert quarantine["raw"]["content"] == "ff00"


def test_timestamp_conversion_preserves_the_instant():
    source = datetime(2026, 8, 1, 12, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    converted = utc_datetime(source)

    assert converted == datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc)
