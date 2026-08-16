"""Unit tests for the pure transformer, including the planted anomaly cases."""

from datetime import datetime, timezone

import pytest

from transform import (
    QUARANTINE_MISSING_DOCUMENT,
    transform_document,
    transform_snapshot,
    version_gap,
)

NS = "demo"
DOCS_SOURCE = "otterworks_demo.documents"
SNAPS_SOURCE = "otterworks_demo.document_snapshots"
MIGRATED_AT = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

DOC_ID = "11111111-1111-4111-8111-111111111111"
OWNER_ID = "22222222-2222-4222-8222-222222222222"
FOLDER_ID = "33333333-3333-4333-8333-333333333333"


def doc_row(**overrides) -> dict:
    row = {
        "id": DOC_ID,
        "title": "Legacy document demo-000001",
        "content": "Body of the document.",
        "content_type": "text/markdown",
        "owner_id": OWNER_ID,
        "folder_id": FOLDER_ID,
        "is_deleted": False,
        "is_template": False,
        "word_count": 4,
        "version": 3,
        "created_at": datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def version_row(number: int, **overrides) -> dict:
    row = {
        "id": f"44444444-4444-4444-8444-{number:012d}",
        "document_id": DOC_ID,
        "version_number": number,
        "title": "Legacy document demo-000001",
        "content": f"rev {number}",
        "created_by": OWNER_ID,
        "created_at": datetime(2026, 7, number, 0, 0, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def snapshot_row(snap_id: str, document_id: str = DOC_ID, **overrides) -> dict:
    row = {
        "id": snap_id,
        "document_id": document_id,
        "state_b64": "c3RhdGU=",
        "label": "autosave",
        "created_by": OWNER_ID,
        "created_at": datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def transform(doc, versions=(), snapshots=()):
    return transform_document(
        doc, list(versions), list(snapshots), NS, DOCS_SOURCE, MIGRATED_AT
    )


def test_complete_document_has_no_version_gap():
    out = transform(doc_row(), [version_row(3), version_row(1), version_row(2)])

    assert out["_id"] == DOC_ID
    assert out["declaredVersion"] == 3
    assert out["versionCount"] == 3
    assert "versionGap" not in out
    assert [v["versionNumber"] for v in out["versions"]] == [1, 2, 3]
    assert out["versions"][0]["versionId"].startswith("44444444")
    assert out["_migration"] == {
        "ns": NS, "sourceTable": DOCS_SOURCE, "migratedAt": MIGRATED_AT
    }


def test_declared_version_is_never_recomputed_and_gap_is_reported():
    # planted anomaly: version 2 of a 3-version document is missing
    out = transform(doc_row(), [version_row(1), version_row(3)])

    assert out["declaredVersion"] == 3  # verbatim from the source column
    assert out["versionCount"] == 2
    assert out["versionGap"] == {"missing": [2], "expected": 3, "present": 2}
    assert [v["versionNumber"] for v in out["versions"]] == [1, 3]


def test_gap_at_the_end_of_the_series_is_reported():
    out = transform(doc_row(version=4), [version_row(n) for n in (1, 2, 3)])

    assert out["versionGap"] == {"missing": [4], "expected": 4, "present": 3}


@pytest.mark.parametrize(
    "declared, present, expected",
    [
        (7, [1, 2, 3, 5, 6, 7], {"missing": [4], "expected": 7, "present": 6}),
        (5, [1, 2, 3, 4, 5], None),
        # counts agree but a revision is missing and an unexpected one is present
        (3, [1, 2, 4], {"missing": [3], "expected": 3, "present": 3}),
        (5, [2, 4], {"missing": [1, 3, 5], "expected": 5, "present": 2}),
        (2, [], {"missing": [1, 2], "expected": 2, "present": 0}),
    ],
)
def test_version_gap_enumerates_missing_numbers(declared, present, expected):
    assert version_gap(declared, present) == expected


def test_null_folder_id_omits_the_field():
    out = transform(doc_row(folder_id=None), [version_row(n) for n in (1, 2, 3)])

    assert "folderId" not in out


def test_folder_id_is_carried_as_a_string():
    assert transform(doc_row())["folderId"] == FOLDER_ID


def test_snapshots_are_referenced_not_embedded():
    snaps = [
        snapshot_row("55555555-5555-4555-8555-555555555555"),
        snapshot_row("00000000-0000-4000-8000-000000000000"),
    ]
    out = transform(doc_row(), [version_row(n) for n in (1, 2, 3)], snaps)

    assert out["snapshotIds"] == [
        "00000000-0000-4000-8000-000000000000",
        "55555555-5555-4555-8555-555555555555",
    ]
    assert "snapshots" not in out
    assert all("stateB64" not in v for v in out["versions"])


def test_document_without_snapshots_has_empty_reference_list():
    assert transform(doc_row())["snapshotIds"] == []


def test_naive_timestamps_are_normalized_to_utc():
    out = transform(doc_row(created_at=datetime(2026, 7, 1, 0, 0, 0)))

    assert out["createdAt"] == datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_snapshot_transform_keeps_the_blob_and_reference():
    out = transform_snapshot(
        snapshot_row("55555555-5555-4555-8555-555555555555"),
        NS, SNAPS_SOURCE, MIGRATED_AT,
    )

    assert out["_id"] == "55555555-5555-4555-8555-555555555555"
    assert out["documentId"] == DOC_ID
    assert out["stateB64"] == "c3RhdGU="
    assert out["label"] == "autosave"
    assert "quarantine_reason" not in out
    assert out["_migration"]["sourceTable"] == SNAPS_SOURCE


def test_orphaned_snapshot_is_quarantined_not_dropped():
    dangling = "99999999-9999-4999-8999-999999999999"
    out = transform_snapshot(
        snapshot_row("66666666-6666-4666-8666-666666666666", document_id=dangling,
                     label="orphan", state_b64="b3JwaGFu"),
        NS, SNAPS_SOURCE, MIGRATED_AT,
        quarantine_reason=QUARANTINE_MISSING_DOCUMENT,
    )

    assert out["quarantine_reason"] == "missing_document"
    assert out["documentId"] == dangling  # dangling reference preserved as-is
    assert out["stateB64"] == "b3JwaGFu"
