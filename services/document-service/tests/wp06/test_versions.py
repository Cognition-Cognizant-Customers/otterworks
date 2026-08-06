"""Version listing, restore edges, and a deterministic two-writer race."""

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.document import DocumentVersion
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.services.document_service import DocumentService
from tests.wp06.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_create_seeds_version_one(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id, content="one two three")
    resp = await client.get(
        f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
    )
    assert resp.status_code == 200
    versions = resp.json()
    assert [v["version_number"] for v in versions] == [1]


async def test_versions_are_listed_in_ascending_order(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    for i in range(2):
        put = await client.put(
            f"/api/v1/documents/{doc['id']}",
            json={"title": f"rev {i}", "content": f"body {i}"},
            headers=auth_headers(owner_id),
        )
        assert put.status_code == 200

    resp = await client.get(
        f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
    )
    assert [v["version_number"] for v in resp.json()] == [1, 2, 3]


async def test_versions_of_unknown_document_returns_404(
    client: AsyncClient, jwt_secret: str, owner_id: uuid.UUID
):
    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/versions", headers=auth_headers(owner_id)
    )
    assert resp.status_code == 404


async def test_versions_of_soft_deleted_document_returns_404(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    assert (
        await client.delete(
            f"/api/v1/documents/{doc['id']}", headers=auth_headers(owner_id)
        )
    ).status_code == 204

    resp = await client.get(
        f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
    )
    assert resp.status_code == 404


# ---- restore ----


async def test_restore_previous_version_reverts_content_and_bumps_version(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id, title="v1 title", content="v1 body")
    await client.put(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "v2 title", "content": "v2 body here"},
        headers=auth_headers(owner_id),
    )
    versions = (
        await client.get(
            f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
        )
    ).json()
    first = next(v for v in versions if v["version_number"] == 1)

    resp = await client.post(
        f"/api/v1/documents/{doc['id']}/versions/{first['id']}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "v1 title"
    assert body["content"] == "v1 body"
    assert body["word_count"] == 2
    assert body["version"] == 3


async def test_restore_unknown_version_returns_404(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    resp = await client.post(
        f"/api/v1/documents/{doc['id']}/versions/{uuid.uuid4()}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 404


async def test_restore_version_belonging_to_another_document_returns_404(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    mine = await create_document(owner_id, title="mine")
    other = await create_document(owner_id, title="other")
    other_versions = (
        await client.get(
            f"/api/v1/documents/{other['id']}/versions", headers=auth_headers(owner_id)
        )
    ).json()

    resp = await client.post(
        f"/api/v1/documents/{mine['id']}/versions/{other_versions[0]['id']}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 404


async def test_restore_on_soft_deleted_document_returns_404(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    """A version of a deleted document is not restorable — the document is gone."""
    doc = await create_document(owner_id)
    versions = (
        await client.get(
            f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
        )
    ).json()
    await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth_headers(owner_id))

    resp = await client.post(
        f"/api/v1/documents/{doc['id']}/versions/{versions[0]['id']}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 404


async def test_restore_is_itself_restorable(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    """Restoring appends a version rather than rewriting history."""
    doc = await create_document(owner_id, title="v1", content="a")
    await client.put(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "v2", "content": "b"},
        headers=auth_headers(owner_id),
    )
    versions = (
        await client.get(
            f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
        )
    ).json()
    await client.post(
        f"/api/v1/documents/{doc['id']}/versions/{versions[0]['id']}/restore",
        headers=auth_headers(owner_id),
    )

    after = (
        await client.get(
            f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
        )
    ).json()
    assert [v["version_number"] for v in after] == [1, 2, 3]
    assert after[2]["content"] == "a"


# ---- two-writer race, deterministic (no threads, no sleeps) ----


async def _seed_document(
    sessionmaker: async_sessionmaker[AsyncSession], owner: uuid.UUID
) -> uuid.UUID:
    async with sessionmaker() as session:
        document = await DocumentService(session).create(
            DocumentCreate(title="shared", content="v1", owner_id=owner)
        )
        return document.id


async def _interleaved_writers(
    sessionmaker: async_sessionmaker[AsyncSession], document_id: uuid.UUID
) -> list[DocumentVersion]:
    """Two writers that both read version 1, then commit one after the other."""
    async with sessionmaker() as session_a, sessionmaker() as session_b:
        service_a, service_b = DocumentService(session_a), DocumentService(session_b)

        # Writer B reads first and holds its (now stale) view of the document.
        before_b = await service_b.get(document_id)
        assert before_b is not None
        assert before_b.version == 1
        await session_b.commit()

        await service_a.update(
            document_id, DocumentUpdate(title="from A", content="written by A")
        )
        await service_b.update(
            document_id, DocumentUpdate(title="from B", content="written by B")
        )

    async with sessionmaker() as verify:
        rows = await verify.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.created_at.asc())
        )
        return list(rows.scalars().all())


async def test_concurrent_version_writes_last_writer_wins(
    file_sessionmaker: async_sessionmaker[AsyncSession], owner_id: uuid.UUID
):
    """Pins today's behaviour: the second writer overwrites the first silently."""
    document_id = await _seed_document(file_sessionmaker, owner_id)
    versions = await _interleaved_writers(file_sessionmaker, document_id)

    async with file_sessionmaker() as session:
        document = await DocumentService(session).get(document_id)
    assert document is not None
    assert document.content == "written by B"
    # Three writes happened but the counter only advanced once past the seed.
    assert document.version == 2
    assert len(versions) == 3


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING WP-06-1: DocumentService.update has no optimistic-locking check, so two "
        "writers that both read version N each write a row numbered N+1. The version history "
        "ends up with duplicate version_number values and one edit is silently lost. There is "
        "no unique constraint on (document_id, version_number) to catch it either."
    ),
)
async def test_concurrent_version_writes_should_not_duplicate_version_numbers(
    file_sessionmaker: async_sessionmaker[AsyncSession], owner_id: uuid.UUID
):
    document_id = await _seed_document(file_sessionmaker, owner_id)
    versions = await _interleaved_writers(file_sessionmaker, document_id)

    numbers = [v.version_number for v in versions]
    assert len(numbers) == len(set(numbers)), f"duplicate version numbers: {numbers}"
