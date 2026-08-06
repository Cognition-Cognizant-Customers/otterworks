"""WP-06: version history, restore, concurrent writes and comment lifecycle."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.document import DocumentVersion
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.services.document_service import DocumentService
from tests._wp06_support import auth_headers, create_document, wp06_jwt_env  # noqa: F401


@pytest.fixture
async def parallel_sessions(tmp_path) -> AsyncGenerator[tuple[AsyncSession, AsyncSession], None]:
    """Two sessions over two connections to a private, file-backed database.

    The in-memory database used by ``conftest`` is served by a ``StaticPool``, so
    every session shares one connection and one transaction — useless for writer
    isolation. A per-test file (``tmp_path`` is unique per test) in WAL mode gives
    two genuinely independent transactions with no cross-test state.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/wp06.db")
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as first, maker() as second:
        yield first, second
    await engine.dispose()


# ---------------------------------------------------------------- positive --


@pytest.mark.asyncio
async def test_versions_are_listed_oldest_first(client: AsyncClient, owner_id: uuid.UUID):
    """Pinned ordering: ``list_versions`` sorts ``version_number`` ascending."""
    document = await create_document(client, owner_id, content="v1")
    for revision in ("v2", "v3"):
        await client.put(
            f"/api/v1/documents/{document['id']}",
            json={"title": "Versioned", "content": revision},
            headers=auth_headers(owner_id),
        )

    resp = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    assert resp.status_code == 200
    numbers = [version["version_number"] for version in resp.json()]
    assert numbers == [1, 2, 3]


@pytest.mark.asyncio
async def test_a_fresh_document_has_exactly_one_version(client: AsyncClient, owner_id: uuid.UUID):
    document = await create_document(client, owner_id)
    resp = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_restore_rolls_content_back_and_appends_a_new_version(
    client: AsyncClient, owner_id: uuid.UUID
):
    document = await create_document(client, owner_id, title="Original", content="first")
    await client.put(
        f"/api/v1/documents/{document['id']}",
        json={"title": "Rewritten", "content": "second"},
        headers=auth_headers(owner_id),
    )

    versions = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    first_version_id = versions.json()[0]["id"]

    resp = await client.post(
        f"/api/v1/documents/{document['id']}/versions/{first_version_id}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    restored = resp.json()
    assert restored["title"] == "Original"
    assert restored["content"] == "first"
    assert restored["version"] == 3

    after = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    assert [v["version_number"] for v in after.json()] == [1, 2, 3]


@pytest.mark.asyncio
async def test_restoring_twice_appends_two_versions_it_is_not_idempotent(
    client: AsyncClient, owner_id: uuid.UUID
):
    """Pinned behaviour: restore always writes history, even when it is a no-op."""
    document = await create_document(client, owner_id, content="first")
    versions = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    version_id = versions.json()[0]["id"]

    for expected_version in (2, 3):
        resp = await client.post(
            f"/api/v1/documents/{document['id']}/versions/{version_id}/restore",
            headers=auth_headers(owner_id),
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == expected_version

    final = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    assert len(final.json()) == 3


@pytest.mark.asyncio
async def test_patch_without_any_known_field_does_not_create_a_version(
    client: AsyncClient, owner_id: uuid.UUID
):
    document = await create_document(client, owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{document['id']}", json={}, headers=auth_headers(owner_id)
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 1

    versions = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    assert len(versions.json()) == 1


# ---------------------------------------------------------------- negative --


@pytest.mark.asyncio
async def test_restore_of_an_unknown_version_is_404(client: AsyncClient, owner_id: uuid.UUID):
    document = await create_document(client, owner_id)
    resp = await client.post(
        f"/api/v1/documents/{document['id']}/versions/{uuid.uuid4()}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_restore_of_a_version_deleted_from_history_is_404(
    client: AsyncClient, owner_id: uuid.UUID, db_session: AsyncSession
):
    """A version row can disappear via the ``ON DELETE CASCADE`` path; restoring
    that id must 404 rather than resurrect anything."""
    document = await create_document(client, owner_id, content="first")
    await client.put(
        f"/api/v1/documents/{document['id']}",
        json={"title": "Second", "content": "second"},
        headers=auth_headers(owner_id),
    )

    versions = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    doomed_id = uuid.UUID(versions.json()[0]["id"])

    row = (
        await db_session.execute(select(DocumentVersion).where(DocumentVersion.id == doomed_id))
    ).scalar_one()
    await db_session.delete(row)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/documents/{document['id']}/versions/{doomed_id}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_restore_of_a_version_belonging_to_another_document_is_404(
    client: AsyncClient, owner_id: uuid.UUID
):
    mine = await create_document(client, owner_id, title="Mine")
    other = await create_document(client, owner_id, title="Other")

    other_versions = await client.get(
        f"/api/v1/documents/{other['id']}/versions", headers=auth_headers(owner_id)
    )
    foreign_version_id = other_versions.json()[0]["id"]

    resp = await client.post(
        f"/api/v1/documents/{mine['id']}/versions/{foreign_version_id}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_versions_of_a_soft_deleted_document_are_404(
    client: AsyncClient, owner_id: uuid.UUID
):
    document = await create_document(client, owner_id)
    await client.delete(f"/api/v1/documents/{document['id']}", headers=auth_headers(owner_id))

    resp = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_restore_on_a_soft_deleted_document_is_404(client: AsyncClient, owner_id: uuid.UUID):
    document = await create_document(client, owner_id)
    versions = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    version_id = versions.json()[0]["id"]

    await client.delete(f"/api/v1/documents/{document['id']}", headers=auth_headers(owner_id))

    resp = await client.post(
        f"/api/v1/documents/{document['id']}/versions/{version_id}/restore",
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_versions_of_an_unknown_document_are_404(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/versions", headers=auth_headers(owner_id)
    )
    assert resp.status_code == 404


# ------------------------------------------------ concurrency / idempotency --


@pytest.mark.asyncio
async def test_two_concurrent_writers_both_produce_version_number_2(
    parallel_sessions: tuple[AsyncSession, AsyncSession], owner_id: uuid.UUID
):
    """FINDING (genuine, unfixed): lost update with a duplicated version number.

    ``DocumentService.update`` is a read-modify-write on ``document.version``
    with no optimistic-locking column and no unique constraint on
    ``(document_id, version_number)``. A writer whose snapshot predates another
    writer's commit computes the same next version number, so the history ends
    up with two rows numbered 2 while the counter advances only once.

    The two writers are driven explicitly rather than through ``asyncio.gather``
    so the interleaving is fixed: letting the event loop decide makes the
    outcome non-deterministic (measured 13/15 duplicated, 2/15 not), which would
    be a flaky test rather than a proof.

    This pins the *current* behaviour; the strict-xfail below states what a
    fixed implementation would produce.
    """
    writer_session, stale_session = parallel_sessions
    writer = DocumentService(writer_session)
    stale = DocumentService(stale_session)

    document = await writer.create(
        DocumentCreate(title="Contended", content="base", owner_id=owner_id)
    )

    # The second writer's snapshot is taken before the first writer commits.
    snapshot = await stale.get(document.id)
    assert snapshot.version == 1

    await writer.update(document.id, DocumentUpdate(title="Writer A", content="from A"))
    await stale.update(document.id, DocumentUpdate(title="Writer B", content="from B"))

    numbers = sorted(v.version_number for v in await writer.list_versions(document.id))
    assert numbers == [1, 2, 2], "expected the duplicate-version defect to persist"

    persisted = await writer.get(document.id)
    await writer_session.refresh(persisted)
    assert persisted.title == "Writer B", "the later commit overwrote the earlier one"
    assert persisted.version == 2, "two updates advanced the counter only once"


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Concurrent updates duplicate version_number 2: no optimistic lock on "
        "Document.version and no unique (document_id, version_number) constraint. "
        "Reported by WP-06; not fixed here (test-only package)."
    ),
)
async def test_concurrent_writers_should_produce_distinct_version_numbers(
    parallel_sessions: tuple[AsyncSession, AsyncSession], owner_id: uuid.UUID
):
    writer_session, stale_session = parallel_sessions
    writer = DocumentService(writer_session)
    stale = DocumentService(stale_session)

    document = await writer.create(
        DocumentCreate(title="Contended", content="base", owner_id=owner_id)
    )
    snapshot = await stale.get(document.id)
    assert snapshot.version == 1

    await writer.update(document.id, DocumentUpdate(title="A", content="from A"))
    await stale.update(document.id, DocumentUpdate(title="B", content="from B"))

    numbers = [v.version_number for v in await writer.list_versions(document.id)]
    assert sorted(numbers) == [1, 2, 3]


@pytest.mark.asyncio
async def test_sequential_writers_on_one_session_number_versions_correctly(
    client: AsyncClient, owner_id: uuid.UUID
):
    """The control for the concurrency case: serialised writes are correct."""
    document = await create_document(client, owner_id)
    for expected_version in (2, 3, 4):
        resp = await client.put(
            f"/api/v1/documents/{document['id']}",
            json={"title": f"rev {expected_version}", "content": "body"},
            headers=auth_headers(owner_id),
        )
        assert resp.json()["version"] == expected_version

    versions = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    numbers = [v["version_number"] for v in versions.json()]
    assert numbers == sorted(set(numbers)) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_deleting_the_same_document_twice_is_404_the_second_time(
    client: AsyncClient, owner_id: uuid.UUID
):
    document = await create_document(client, owner_id)
    first = await client.delete(
        f"/api/v1/documents/{document['id']}", headers=auth_headers(owner_id)
    )
    second = await client.delete(
        f"/api/v1/documents/{document['id']}", headers=auth_headers(owner_id)
    )
    assert first.status_code == 204
    assert second.status_code == 404


@pytest.mark.asyncio
async def test_concurrent_comment_writers_both_persist(
    parallel_sessions: tuple[AsyncSession, AsyncSession], owner_id: uuid.UUID
):
    from app.schemas.document import CommentCreate

    first_session, second_session = parallel_sessions
    first = DocumentService(first_session)
    second = DocumentService(second_session)

    document = await first.create(DocumentCreate(title="Discussed", content="", owner_id=owner_id))

    left = await first.add_comment(document.id, CommentCreate(author_id=owner_id, content="from A"))
    right = await second.add_comment(
        document.id, CommentCreate(author_id=owner_id, content="from B")
    )

    assert left is not None and right is not None
    assert left.id != right.id
    assert len(await first.list_comments(document.id)) == 2


# ------------------------------------------------------------- comments --


@pytest.mark.asyncio
async def test_comment_on_a_soft_deleted_document_is_404(client: AsyncClient, owner_id: uuid.UUID):
    document = await create_document(client, owner_id)
    await client.delete(f"/api/v1/documents/{document['id']}", headers=auth_headers(owner_id))

    resp = await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(owner_id), "content": "still here?"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_comment_on_an_unknown_document_is_404(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/comments",
        json={"author_id": str(owner_id), "content": "hello"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_comments_of_a_soft_deleted_document_remain_readable(
    client: AsyncClient, owner_id: uuid.UUID
):
    """FINDING (genuine, unfixed): ``list_comments`` never checks the document.

    Deleting a document hides it from every document route but leaves its
    comment thread world-readable. Pinned here; the strict-xfail below states
    the behaviour a fix should produce.
    """
    document = await create_document(client, owner_id)
    await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(owner_id), "content": "secret"},
    )
    await client.delete(f"/api/v1/documents/{document['id']}", headers=auth_headers(owner_id))

    resp = await client.get(f"/api/v1/documents/{document['id']}/comments")
    assert resp.status_code == 200
    assert [c["content"] for c in resp.json()] == ["secret"]


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Comments of a soft-deleted document stay readable: list_comments does "
        "not resolve the parent document. Reported by WP-06; not fixed here."
    ),
)
async def test_comments_of_a_soft_deleted_document_should_be_404(
    client: AsyncClient, owner_id: uuid.UUID
):
    document = await create_document(client, owner_id)
    await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(owner_id), "content": "secret"},
    )
    await client.delete(f"/api/v1/documents/{document['id']}", headers=auth_headers(owner_id))

    resp = await client.get(f"/api/v1/documents/{document['id']}/comments")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_comments_of_an_unknown_document_list_as_empty(client: AsyncClient):
    resp = await client.get(f"/api/v1/documents/{uuid.uuid4()}/comments")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_comment_content_lower_bound(client: AsyncClient, owner_id: uuid.UUID):
    """``content`` is ``min_length=1``: 0 rejected, 1 accepted."""
    document = await create_document(client, owner_id)

    empty = await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(owner_id), "content": ""},
    )
    assert empty.status_code == 422

    single = await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(owner_id), "content": "x"},
    )
    assert single.status_code == 201


@pytest.mark.asyncio
async def test_comment_requires_an_author_id(client: AsyncClient, owner_id: uuid.UUID):
    document = await create_document(client, owner_id)
    resp = await client.post(
        f"/api/v1/documents/{document['id']}/comments", json={"content": "anonymous"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_deleting_an_unknown_comment_is_404(client: AsyncClient, owner_id: uuid.UUID):
    document = await create_document(client, owner_id)
    resp = await client.delete(f"/api/v1/documents/{document['id']}/comments/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_comment_through_the_wrong_document_is_404(
    client: AsyncClient, owner_id: uuid.UUID
):
    document = await create_document(client, owner_id, title="Owner of the comment")
    other = await create_document(client, owner_id, title="Unrelated")

    created = await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(owner_id), "content": "mine"},
    )
    comment_id = created.json()["id"]

    wrong_parent = await client.delete(f"/api/v1/documents/{other['id']}/comments/{comment_id}")
    assert wrong_parent.status_code == 404

    right_parent = await client.delete(f"/api/v1/documents/{document['id']}/comments/{comment_id}")
    assert right_parent.status_code == 204


@pytest.mark.asyncio
async def test_deleting_a_comment_twice_is_404_the_second_time(
    client: AsyncClient, owner_id: uuid.UUID
):
    document = await create_document(client, owner_id)
    created = await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(owner_id), "content": "transient"},
    )
    comment_id = created.json()["id"]

    first = await client.delete(f"/api/v1/documents/{document['id']}/comments/{comment_id}")
    second = await client.delete(f"/api/v1/documents/{document['id']}/comments/{comment_id}")
    assert first.status_code == 204
    assert second.status_code == 404
