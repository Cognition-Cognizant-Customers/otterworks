"""Metadata filtering for the document list endpoint.

The list endpoint supports ad-hoc metadata filters (title fragment, content
type) and caller-chosen ordering. The repository builds the predicate list for
those filters and reads the ``documents`` table directly.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

COLUMNS = (
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

# ORDER BY identifiers cannot be bound as parameters, so caller-chosen ordering
# is resolved against these allow-lists instead.
SORTABLE_COLUMNS = frozenset(COLUMNS)
DIRECTIONS = frozenset(("asc", "desc"))


class DocumentQueryRepository:
    """Reads the document table for the list endpoint's metadata filters."""

    def __init__(self, db: AsyncSession):
        self.db = db

    def _where(
        self,
        owner_id: str | None,
        title_contains: str | None,
        content_type: str | None,
        folder_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        clauses = ["is_deleted = false", "is_template = false"]
        params: dict[str, Any] = {}
        if owner_id:
            clauses.append("owner_id = :owner_id")
            params["owner_id"] = owner_id
        if folder_id:
            clauses.append("folder_id = :folder_id")
            params["folder_id"] = folder_id
        if title_contains:
            clauses.append("lower(title) LIKE lower(:title_contains)")
            params["title_contains"] = f"%{title_contains}%"
        if content_type:
            clauses.append("content_type = :content_type")
            params["content_type"] = content_type
        return " AND ".join(clauses), params

    async def count_documents(
        self,
        *,
        owner_id: str | None = None,
        title_contains: str | None = None,
        content_type: str | None = None,
        folder_id: str | None = None,
    ) -> int:
        """Count documents matching the metadata filters."""
        where, params = self._where(owner_id, title_contains, content_type, folder_id)
        sql = "SELECT count(*) FROM documents WHERE " + where
        result = await self.db.execute(text(sql), params)
        return int(result.scalar_one())

    async def search_documents(
        self,
        *,
        owner_id: str | None = None,
        title_contains: str | None = None,
        content_type: str | None = None,
        folder_id: str | None = None,
        sort: str = "updated_at",
        direction: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return document rows matching the metadata filters, newest first."""
        sort_column = sort.lower()
        if sort_column not in SORTABLE_COLUMNS:
            raise ValueError(f"unsupported sort column: {sort!r}")
        sort_direction = direction.lower()
        if sort_direction not in DIRECTIONS:
            raise ValueError(f"unsupported sort direction: {direction!r}")
        where, params = self._where(owner_id, title_contains, content_type, folder_id)
        sql = (
            f"SELECT {', '.join(COLUMNS)} FROM documents WHERE "
            + where
            + f" ORDER BY {sort_column} {sort_direction} LIMIT :limit OFFSET :offset"
        )
        params["limit"] = limit
        params["offset"] = offset
        logger.debug("document_filter_query", sort=sort_column, direction=sort_direction)
        result = await self.db.execute(text(sql), params)
        return [dict(row._mapping) for row in result]
