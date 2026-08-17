"""Document business logic service."""

import html as html_mod
import math
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mongo import MongoDocument, MongoDocumentStore, MongoVersion
from app.db.session import mongo_store
from app.models.document import Comment, Template
from app.schemas.document import (
    CommentCreate,
    DocumentCreate,
    DocumentFromTemplate,
    DocumentPatch,
    DocumentUpdate,
    TemplateCreate,
)
from app.services.event_publisher import event_publisher

logger = structlog.get_logger()


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _document_index_payload(document: MongoDocument) -> dict[str, object]:
    return {
        "id": document.id,
        "title": document.title,
        "content": document.content,
        "owner_id": document.owner_id,
        "tags": [],
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


class DocumentService:
    def __init__(
        self, db: AsyncSession, documents: MongoDocumentStore = mongo_store
    ) -> None:
        self.db = db
        self.documents = documents

    # ---- Document CRUD ----

    async def create(self, data: DocumentCreate) -> MongoDocument:
        if data.owner_id is None:
            raise ValueError("owner_id is required")
        document = await self.documents.create(
            title=data.title,
            content=data.content,
            content_type=data.content_type,
            owner_id=data.owner_id,
            folder_id=data.folder_id,
        )
        await event_publisher.publish(
            "document_created",
            _document_index_payload(document),
        )
        return document

    async def get(self, document_id: UUID) -> MongoDocument | None:
        return await self.documents.get(document_id)

    async def list_documents(
        self,
        owner_id: UUID | None = None,
        folder_id: UUID | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[MongoDocument], int]:
        return await self.documents.list_documents(owner_id, folder_id, page, size)

    @staticmethod
    def _append_version(document: MongoDocument, created_by: UUID) -> None:
        document.version += 1
        document.updated_at = datetime.now(UTC)
        document.versions.append(
            MongoVersion(
                id=uuid4(),
                document_id=document.id,
                version_number=document.version,
                title=document.title,
                content=document.content,
                created_by=created_by,
                created_at=document.updated_at,
            )
        )

    async def update(
        self, document_id: UUID, data: DocumentUpdate, updated_by: UUID | None = None
    ) -> MongoDocument | None:
        document = await self.get(document_id)
        if not document:
            return None
        document.title = data.title
        document.content = data.content
        document.content_type = data.content_type
        document.folder_id = data.folder_id
        document.word_count = _word_count(data.content)
        self._append_version(document, updated_by or document.owner_id)
        await self.documents.save(document)
        await event_publisher.publish(
            "document_updated",
            _document_index_payload(document),
        )
        return document

    async def patch(
        self, document_id: UUID, data: DocumentPatch
    ) -> MongoDocument | None:
        document = await self.get(document_id)
        if not document:
            return None
        changed = False
        if "title" in data.model_fields_set and data.title is not None:
            document.title = data.title
            changed = True
        if "content" in data.model_fields_set and data.content is not None:
            document.content = data.content
            document.word_count = _word_count(data.content)
            changed = True
        if "content_type" in data.model_fields_set and data.content_type is not None:
            document.content_type = data.content_type
            changed = True
        if "folder_id" in data.model_fields_set:
            document.folder_id = data.folder_id
            changed = True
        if changed:
            self._append_version(document, document.owner_id)
            await self.documents.save(document)
            await event_publisher.publish(
                "document_updated",
                _document_index_payload(document),
            )
        return document

    async def delete(self, document_id: UUID) -> bool:
        document = await self.get(document_id)
        if not document:
            return False
        document.is_deleted = True
        document.updated_at = datetime.now(UTC)
        await self.documents.save(document)
        await event_publisher.publish(
            "document_deleted", {"id": document_id, "type": "document"}
        )
        return True

    # ---- Versions ----

    async def list_versions(self, document_id: UUID) -> list[MongoVersion]:
        document = await self.get(document_id)
        if document is None:
            return []
        return sorted(
            document.versions, key=lambda version: version.version_number, reverse=True
        )

    async def restore_version(
        self, document_id: UUID, version_id: UUID
    ) -> MongoDocument | None:
        document = await self.get(document_id)
        if not document:
            return None
        version = next(
            (item for item in document.versions if item.id == version_id), None
        )
        if version is None:
            return None
        document.title = version.title
        document.content = version.content
        document.word_count = _word_count(version.content)
        self._append_version(document, document.owner_id)
        await self.documents.save(document)
        await event_publisher.publish(
            "document_updated",
            {
                **_document_index_payload(document),
                "restored_from": version_id,
            },
        )
        return document

    # ---- Search ----

    async def search(
        self, query: str, page: int = 1, size: int = 20
    ) -> tuple[list[MongoDocument], int]:
        return await self.documents.search(query, page, size)

    def export_document(
        self, document: MongoDocument, fmt: str
    ) -> tuple[str, str]:
        if fmt == "html":
            safe_title = html_mod.escape(document.title)
            safe_content = html_mod.escape(document.content)
            markup = (
                f"<html><head><title>{safe_title}</title></head>"
                f"<body><h1>{safe_title}</h1>"
                f"<div>{safe_content}</div></body></html>"
            )
            return markup, "text/html"
        if fmt == "markdown":
            md = f"# {document.title}\n\n{document.content}"
            return md, "text/markdown"
        # pdf → return simple text representation
        text = f"TITLE: {document.title}\n\n{document.content}"
        return text, "application/pdf"

    # ---- Comments ----

    async def add_comment(
        self, document_id: UUID, data: CommentCreate
    ) -> Comment | None:
        document = await self.get(document_id)
        if not document:
            return None

        comment = Comment(
            document_id=document_id,
            author_id=data.author_id,
            content=data.content,
        )
        self.db.add(comment)
        await self.db.commit()
        await self.db.refresh(comment)

        await event_publisher.publish(
            "comment_added",
            {
                "comment_id": comment.id,
                "document_id": document_id,
                "author_id": data.author_id,
            },
        )
        return comment

    async def list_comments(self, document_id: UUID) -> list[Comment]:
        result = await self.db.execute(
            select(Comment)
            .where(Comment.document_id == document_id)
            .order_by(Comment.created_at.asc())
        )
        return list(result.scalars().all())

    async def delete_comment(self, document_id: UUID, comment_id: UUID) -> bool:
        result = await self.db.execute(
            select(Comment).where(
                Comment.id == comment_id, Comment.document_id == document_id
            )
        )
        comment = result.scalar_one_or_none()
        if not comment:
            return False
        await self.db.delete(comment)
        await self.db.commit()
        return True

    # ---- Templates ----

    async def create_template(self, data: TemplateCreate) -> Template:
        template = Template(
            name=data.name,
            description=data.description,
            content=data.content,
            content_type=data.content_type,
            created_by=data.created_by,
        )
        self.db.add(template)
        await self.db.commit()
        await self.db.refresh(template)
        return template

    async def list_templates(self) -> list[Template]:
        result = await self.db.execute(
            select(Template).order_by(Template.name.asc())
        )
        return list(result.scalars().all())

    async def get_template(self, template_id: UUID) -> Template | None:
        result = await self.db.execute(
            select(Template).where(Template.id == template_id)
        )
        return result.scalar_one_or_none()

    async def create_from_template(
        self, template_id: UUID, data: DocumentFromTemplate
    ) -> MongoDocument | None:
        template = await self.get_template(template_id)
        if not template:
            return None

        create_data = DocumentCreate(
            title=data.title,
            content=template.content,
            content_type=template.content_type,
            owner_id=data.owner_id,
            folder_id=data.folder_id,
        )
        return await self.create(create_data)

    # ---- Helpers ----

    @staticmethod
    def paginate(total: int, page: int, size: int) -> int:
        return max(1, math.ceil(total / size)) if size > 0 else 1
