"""MongoDB persistence for migrated documents and embedded versions."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from pymongo import DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import settings


@dataclass
class MongoVersion:
    id: UUID
    document_id: UUID
    version_number: int
    title: str
    content: str
    created_by: UUID
    created_at: datetime


@dataclass
class MongoDocument:
    id: UUID
    title: str
    content: str
    content_type: str
    owner_id: UUID
    folder_id: UUID | None
    is_deleted: bool
    is_template: bool
    word_count: int
    version: int
    created_at: datetime
    updated_at: datetime
    versions: list[MongoVersion]


def _string(record: dict[str, object], key: str) -> str:
    return cast(str, record[key])


def _datetime(record: dict[str, object], key: str) -> datetime:
    value = cast(datetime, record[key])
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _uuid(record: dict[str, object], key: str) -> UUID:
    return UUID(_string(record, key))


def _version(document_id: UUID, record: dict[str, object]) -> MongoVersion:
    return MongoVersion(
        id=UUID(_string(record, "_id")),
        document_id=document_id,
        version_number=cast(int, record["version_number"]),
        title=_string(record, "title"),
        content=_string(record, "content"),
        created_by=UUID(_string(record, "created_by")),
        created_at=_datetime(record, "created_at"),
    )


def _document(record: dict[str, object]) -> MongoDocument:
    document_id = UUID(_string(record, "_id"))
    folder_value = record.get("folder_id")
    return MongoDocument(
        id=document_id,
        title=_string(record, "title"),
        content=_string(record, "content"),
        content_type=_string(record, "content_type"),
        owner_id=_uuid(record, "owner_id"),
        folder_id=UUID(cast(str, folder_value)) if folder_value else None,
        is_deleted=cast(bool, record["is_deleted"]),
        is_template=cast(bool, record["is_template"]),
        word_count=cast(int, record["word_count"]),
        version=cast(int, record["declared_version"]),
        created_at=_datetime(record, "created_at"),
        updated_at=_datetime(record, "updated_at"),
        versions=[
            _version(document_id, cast(dict[str, object], version))
            for version in cast(list[object], record["versions"])
        ],
    )


def _version_record(version: MongoVersion) -> dict[str, object]:
    return {
        "_id": str(version.id),
        "version_number": version.version_number,
        "title": version.title,
        "content": version.content,
        "created_by": str(version.created_by),
        "created_at": version.created_at,
    }


def _document_record(document: MongoDocument, namespace: str) -> dict[str, object]:
    present_versions = {version.version_number for version in document.versions}
    record: dict[str, object] = {
        "_id": str(document.id),
        "ns": namespace,
        "title": document.title,
        "content": document.content,
        "content_type": document.content_type,
        "owner_id": str(document.owner_id),
        "is_deleted": document.is_deleted,
        "is_template": document.is_template,
        "word_count": document.word_count,
        "declared_version": document.version,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "versions": [_version_record(version) for version in document.versions],
        "version_sequence": {
            "declared": document.version,
            "present": len(document.versions),
            "missing": [
                version_number
                for version_number in range(1, document.version + 1)
                if version_number not in present_versions
            ],
        },
    }
    if document.folder_id is not None:
        record["folder_id"] = str(document.folder_id)
    return record


class MongoDocumentStore:
    """Async MongoDB store scoped to one namespace."""

    def __init__(self) -> None:
        self.client: MongoClient[dict[str, object]] = MongoClient(settings.mongo_uri)
        self.database: Database[dict[str, object]] = self.client[settings.mongo_db]
        self.documents: Collection[dict[str, object]] = self.database["documents"]

    def _filter(self, document_id: UUID | None = None) -> dict[str, object]:
        query: dict[str, object] = {"ns": settings.namespace}
        if document_id is not None:
            query["_id"] = str(document_id)
        return query

    async def get(self, document_id: UUID, include_deleted: bool = False) -> MongoDocument | None:
        query = self._filter(document_id)
        if not include_deleted:
            query["is_deleted"] = False
        record = await asyncio.to_thread(self.documents.find_one, query)
        return _document(record) if record is not None else None

    async def list_documents(
        self,
        owner_id: UUID | None,
        folder_id: UUID | None,
        page: int,
        size: int,
    ) -> tuple[list[MongoDocument], int]:
        query = self._filter()
        query.update({"is_deleted": False, "is_template": False})
        if owner_id is not None:
            query["owner_id"] = str(owner_id)
        if folder_id is not None:
            query["folder_id"] = str(folder_id)
        total = await asyncio.to_thread(self.documents.count_documents, query)
        def fetch() -> list[dict[str, object]]:
            cursor = (
                self.documents.find(query)
                .sort("updated_at", DESCENDING)
                .skip((page - 1) * size)
                .limit(size)
            )
            return list(cursor)
        return [_document(record) for record in await asyncio.to_thread(fetch)], total

    async def search(
        self, query_text: str, page: int, size: int
    ) -> tuple[list[MongoDocument], int]:
        literal_query = re.escape(query_text)
        query = self._filter()
        query.update(
            {
                "is_deleted": False,
                "is_template": False,
                "$or": [
                    {"title": {"$regex": literal_query, "$options": "i"}},
                    {"content": {"$regex": literal_query, "$options": "i"}},
                ],
            }
        )
        total = await asyncio.to_thread(self.documents.count_documents, query)
        def fetch() -> list[dict[str, object]]:
            cursor = (
                self.documents.find(query)
                .sort("updated_at", DESCENDING)
                .skip((page - 1) * size)
                .limit(size)
            )
            return list(cursor)
        return [_document(record) for record in await asyncio.to_thread(fetch)], total

    async def create(
        self,
        title: str,
        content: str,
        content_type: str,
        owner_id: UUID,
        folder_id: UUID | None,
    ) -> MongoDocument:
        now = datetime.now(UTC)
        document_id = uuid4()
        version = MongoVersion(
            id=uuid4(),
            document_id=document_id,
            version_number=1,
            title=title,
            content=content,
            created_by=owner_id,
            created_at=now,
        )
        document = MongoDocument(
            id=document_id,
            title=title,
            content=content,
            content_type=content_type,
            owner_id=owner_id,
            folder_id=folder_id,
            is_deleted=False,
            is_template=False,
            word_count=len(content.split()) if content else 0,
            version=1,
            created_at=now,
            updated_at=now,
            versions=[version],
        )
        await asyncio.to_thread(
            self.documents.insert_one, _document_record(document, settings.namespace)
        )
        return document

    async def save(self, document: MongoDocument) -> MongoDocument:
        await asyncio.to_thread(
            self.documents.replace_one,
            self._filter(document.id),
            _document_record(document, settings.namespace),
            upsert=False,
        )
        return document

    def close(self) -> None:
        self.client.close()
