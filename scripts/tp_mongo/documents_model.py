"""Pure document migration model logic.

This module deliberately has no database-driver dependencies.  Database
orchestration lives in ``migrate_documents.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

VERSION_ARRAY_BOUND = 50

DOCUMENT_COLUMNS = (
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
VERSION_COLUMNS = (
    "id",
    "document_id",
    "version_number",
    "title",
    "content",
    "created_by",
    "created_at",
)
SNAPSHOT_COLUMNS = (
    "id",
    "document_id",
    "state_b64",
    "label",
    "created_by",
    "created_at",
)


def utc_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def decode_text(value: Any) -> tuple[Any, str | None]:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        try:
            return raw.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, raw.hex()
    return value, None


def source_id_text(value: Any) -> str:
    decoded, raw_hex = decode_text(value)
    if raw_hex is not None:
        return f"bytes:{raw_hex}"
    if decoded is None:
        return "<null>"
    return str(decoded)


def raw_value(value: Any) -> Any:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, datetime):
        try:
            return utc_datetime(value).isoformat()
        except (TypeError, ValueError):
            return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def raw_record(columns: tuple[str, ...], row: tuple[Any, ...]) -> dict[str, Any]:
    return {column: raw_value(value) for column, value in zip(columns, row)}


def quarantine_record(
    ns: str,
    source_table: str,
    source_id: Any,
    reason: str,
    detail: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    source_id_value = source_id_text(source_id)
    return {
        "_id": f"{source_table}:{source_id_value}",
        "ns": ns,
        "source_table": source_table,
        "source_id": source_id_value,
        "reason": reason,
        "detail": detail,
        "raw": raw,
    }


STRING = {"bsonType": "string"}
INTEGER = {"bsonType": ["int", "long"]}
DATE = {"bsonType": "date"}

VERSION_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id",
        "version_number",
        "title",
        "content",
        "created_by",
        "created_at",
    ],
    "properties": {
        "_id": STRING,
        "version_number": INTEGER,
        "title": STRING,
        "content": STRING,
        "created_by": STRING,
        "created_at": DATE,
    },
    "additionalProperties": False,
}

DOCUMENTS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id",
            "ns",
            "title",
            "content",
            "content_type",
            "owner_id",
            "is_deleted",
            "is_template",
            "word_count",
            "declared_version",
            "created_at",
            "updated_at",
            "versions",
            "version_sequence",
        ],
        "properties": {
            "_id": STRING,
            "ns": STRING,
            "title": STRING,
            "content": STRING,
            "content_type": STRING,
            "owner_id": STRING,
            "folder_id": STRING,
            "is_deleted": {"bsonType": "bool"},
            "is_template": {"bsonType": "bool"},
            "word_count": INTEGER,
            "declared_version": INTEGER,
            "created_at": DATE,
            "updated_at": DATE,
            "versions": {
                "bsonType": "array",
                "items": VERSION_SCHEMA,
            },
            "version_sequence": {
                "bsonType": "object",
                "required": ["declared", "present", "missing"],
                "properties": {
                    "declared": INTEGER,
                    "present": INTEGER,
                    "missing": {
                        "bsonType": "array",
                        "items": INTEGER,
                    },
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
}

SNAPSHOTS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id",
            "ns",
            "document_id",
            "state_b64",
            "created_by",
            "created_at",
            "parent_missing",
        ],
        "properties": {
            "_id": STRING,
            "ns": STRING,
            "document_id": STRING,
            "state_b64": STRING,
            "label": STRING,
            "created_by": STRING,
            "created_at": DATE,
            "parent_missing": {"bsonType": "bool"},
        },
        "additionalProperties": False,
    }
}

QUARANTINE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "ns", "source_table", "source_id", "reason", "detail"],
        "properties": {
            "_id": STRING,
            "ns": STRING,
            "source_table": STRING,
            "source_id": STRING,
            "reason": STRING,
            "detail": STRING,
            "raw": {"bsonType": "object"},
        },
        "additionalProperties": False,
    }
}

VALIDATORS = {
    "documents": DOCUMENTS_VALIDATOR,
    "document_snapshots": SNAPSHOTS_VALIDATOR,
    "documents_quarantine": QUARANTINE_VALIDATOR,
}


def missing_versions_for(declared: int, present: list[int]) -> list[int]:
    present_set = set(present)
    return [number for number in range(1, declared + 1) if number not in present_set]


def document_id_from_row(row: tuple[Any, ...]) -> str | None:
    value = row[0]
    if value is None:
        return None
    decoded, raw_hex = decode_text(value)
    if raw_hex is not None:
        return f"bytes:{raw_hex}"
    return str(decoded)


def process_version(
    ns: str,
    row: tuple[Any, ...],
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    raw = raw_record(VERSION_COLUMNS, row)
    source_id = row[0]
    source_key = source_id_text(source_id)
    required = {
        "id": row[0],
        "document_id": row[1],
        "version_number": row[2],
        "title": row[3],
        "content": row[4],
        "created_by": row[5],
        "created_at": row[6],
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return (
            str(row[1]) if row[1] is not None else "<null>",
            None,
            quarantine_record(
                ns,
                "document_versions",
                source_id,
                "null_required_field",
                f"required field(s) are NULL: {', '.join(missing)}",
                raw,
            ),
        )

    title, title_hex = decode_text(row[3])
    content, content_hex = decode_text(row[4])
    encoding_errors = [
        name
        for name, value in (("title", title_hex), ("content", content_hex))
        if value is not None
    ]
    if encoding_errors:
        return (
            str(row[1]),
            None,
            quarantine_record(
                ns,
                "document_versions",
                source_id,
                "invalid_utf8",
                f"invalid UTF-8 in field(s): {', '.join(encoding_errors)}; raw bytes are hex-encoded",
                raw,
            ),
        )

    try:
        created_at = utc_datetime(row[6])
    except (TypeError, ValueError) as exc:
        return (
            str(row[1]),
            None,
            quarantine_record(
                ns,
                "document_versions",
                source_id,
                "invalid_value",
                f"created_at: {exc}",
                raw,
            ),
        )

    return (
        str(row[1]),
        {
            "_id": source_key,
            "version_number": int(row[2]),
            "title": title,
            "content": content,
            "created_by": str(row[5]),
            "created_at": created_at,
        },
        None,
    )


def process_document(
    ns: str,
    row: tuple[Any, ...],
    version_rows: list[dict[str, Any] | None],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    raw = raw_record(DOCUMENT_COLUMNS, row)
    source_id = row[0]
    required = {
        "id": row[0],
        "title": row[1],
        "content": row[2],
        "content_type": row[3],
        "owner_id": row[4],
        "is_deleted": row[6],
        "is_template": row[7],
        "word_count": row[8],
        "version": row[9],
        "created_at": row[10],
        "updated_at": row[11],
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return (
            None,
            quarantine_record(
                ns,
                "documents",
                source_id,
                "null_required_field",
                f"required field(s) are NULL: {', '.join(missing)}",
                raw,
            ),
            False,
        )

    title, title_hex = decode_text(row[1])
    content, content_hex = decode_text(row[2])
    content_type, content_type_hex = decode_text(row[3])
    folder_id, folder_hex = decode_text(row[5])
    encoding_errors = [
        name
        for name, value in (
            ("title", title_hex),
            ("content", content_hex),
            ("content_type", content_type_hex),
            ("folder_id", folder_hex),
        )
        if value is not None
    ]
    if encoding_errors:
        return (
            None,
            quarantine_record(
                ns,
                "documents",
                source_id,
                "invalid_utf8",
                f"invalid UTF-8 in field(s): {', '.join(encoding_errors)}; raw bytes are hex-encoded",
                raw,
            ),
            False,
        )

    try:
        created_at = utc_datetime(row[10])
        updated_at = utc_datetime(row[11])
    except (TypeError, ValueError) as exc:
        return (
            None,
            quarantine_record(
                ns,
                "documents",
                source_id,
                "invalid_value",
                str(exc),
                raw,
            ),
            False,
        )

    valid_versions = [version for version in version_rows if version is not None]
    if len(version_rows) > VERSION_ARRAY_BOUND:
        return (
            None,
            quarantine_record(
                ns,
                "documents",
                source_id,
                "version_array_over_bound",
                f"{len(version_rows)} versions exceed bound {VERSION_ARRAY_BOUND}",
                raw,
            ),
            False,
        )

    valid_versions.sort(key=lambda version: version["version_number"])
    present = [version["version_number"] for version in valid_versions]
    missing_versions = missing_versions_for(int(row[9]), present)
    document = {
        "_id": source_id_text(source_id),
        "ns": ns,
        "title": title,
        "content": content,
        "content_type": content_type,
        "owner_id": str(row[4]),
        "is_deleted": bool(row[6]),
        "is_template": bool(row[7]),
        "word_count": int(row[8]),
        "declared_version": int(row[9]),
        "created_at": created_at,
        "updated_at": updated_at,
        "versions": valid_versions,
        "version_sequence": {
            "declared": int(row[9]),
            "present": len(present),
            "missing": missing_versions,
        },
    }
    if folder_id is not None:
        document["folder_id"] = str(folder_id)
    return document, None, bool(missing_versions)


def process_snapshot(
    ns: str,
    row: tuple[Any, ...],
    source_document_ids: set[str],
    quarantined_document_ids: set[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    raw = raw_record(SNAPSHOT_COLUMNS, row)
    source_id = row[0]
    required = {
        "id": row[0],
        "document_id": row[1],
        "state_b64": row[2],
        "created_by": row[4],
        "created_at": row[5],
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return (
            None,
            quarantine_record(
                ns,
                "document_snapshots",
                source_id,
                "null_required_field",
                f"required field(s) are NULL: {', '.join(missing)}",
                raw,
            ),
            False,
        )

    state_b64, state_hex = decode_text(row[2])
    label, label_hex = decode_text(row[3])
    encoding_errors = [
        name
        for name, value in (("state_b64", state_hex), ("label", label_hex))
        if value is not None
    ]
    if encoding_errors:
        return (
            None,
            quarantine_record(
                ns,
                "document_snapshots",
                source_id,
                "invalid_utf8",
                f"invalid UTF-8 in field(s): {', '.join(encoding_errors)}; raw bytes are hex-encoded",
                raw,
            ),
            False,
        )

    try:
        created_at = utc_datetime(row[5])
    except (TypeError, ValueError) as exc:
        return (
            None,
            quarantine_record(
                ns,
                "document_snapshots",
                source_id,
                "invalid_value",
                str(exc),
                raw,
            ),
            False,
        )

    document_id = str(row[1])
    if document_id in quarantined_document_ids:
        return (
            None,
            quarantine_record(
                ns,
                "document_snapshots",
                source_id,
                "parent_quarantined",
                f"parent document {document_id} was quarantined and not written to documents",
                raw,
            ),
            False,
        )
    snapshot = {
        "_id": source_id_text(source_id),
        "ns": ns,
        "document_id": document_id,
        "state_b64": state_b64,
        "created_by": str(row[4]),
        "created_at": created_at,
        "parent_missing": document_id not in source_document_ids,
    }
    if label is not None:
        snapshot["label"] = str(label)
    return snapshot, None, bool(snapshot["parent_missing"])
