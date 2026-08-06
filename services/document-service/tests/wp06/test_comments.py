"""Comment edges: deleted/nonexistent documents, validation, and delete scoping."""

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from tests.wp06.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_comment_on_deleted_document_returns_404(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth_headers(owner_id))

    resp = await client.post(
        f"/api/v1/documents/{doc['id']}/comments",
        json={"author_id": str(owner_id), "content": "late comment"},
    )
    assert resp.status_code == 404


async def test_comment_on_nonexistent_document_returns_404(
    client: AsyncClient, owner_id: uuid.UUID
):
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/comments",
        json={"author_id": str(owner_id), "content": "hello"},
    )
    assert resp.status_code == 404


async def test_comment_on_malformed_document_id_returns_422(
    client: AsyncClient, owner_id: uuid.UUID
):
    resp = await client.post(
        "/api/v1/documents/not-a-uuid/comments",
        json={"author_id": str(owner_id), "content": "hello"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"content": "no author"},
        {"author_id": "not-a-uuid", "content": "bad author"},
        {"author_id": str(uuid.uuid4()), "content": ""},
        {"author_id": str(uuid.uuid4())},
        {"author_id": str(uuid.uuid4()), "content": None},
    ],
)
async def test_comment_validation_rejects_bad_payloads(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID, body: dict
):
    doc = await create_document(owner_id)
    resp = await client.post(f"/api/v1/documents/{doc['id']}/comments", json=body)
    assert resp.status_code == 422


async def test_single_character_comment_is_accepted(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    """min_length=1 boundary: one character is the smallest valid comment."""
    doc = await create_document(owner_id)
    resp = await client.post(
        f"/api/v1/documents/{doc['id']}/comments",
        json={"author_id": str(owner_id), "content": "x"},
    )
    assert resp.status_code == 201
    assert resp.json()["content"] == "x"


async def test_delete_comment_with_mismatched_document_returns_404(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id, title="a")
    other = await create_document(owner_id, title="b")
    comment = (
        await client.post(
            f"/api/v1/documents/{doc['id']}/comments",
            json={"author_id": str(owner_id), "content": "scoped"},
        )
    ).json()

    resp = await client.delete(
        f"/api/v1/documents/{other['id']}/comments/{comment['id']}"
    )
    assert resp.status_code == 404

    still_there = await client.get(f"/api/v1/documents/{doc['id']}/comments")
    assert [c["id"] for c in still_there.json()] == [comment["id"]]


async def test_delete_comment_is_not_idempotent_second_call_404s(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    comment = (
        await client.post(
            f"/api/v1/documents/{doc['id']}/comments",
            json={"author_id": str(owner_id), "content": "bye"},
        )
    ).json()

    assert (
        await client.delete(f"/api/v1/documents/{doc['id']}/comments/{comment['id']}")
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/documents/{doc['id']}/comments/{comment['id']}")
    ).status_code == 404


async def test_list_comments_of_nonexistent_document_returns_empty_list(
    client: AsyncClient
):
    """Pins current behaviour: the list route never checks the document exists."""
    resp = await client.get(f"/api/v1/documents/{uuid.uuid4()}/comments")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_comments_survive_soft_delete_of_document(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    """Soft delete hides the document but its comments remain readable."""
    doc = await create_document(owner_id)
    await client.post(
        f"/api/v1/documents/{doc['id']}/comments",
        json={"author_id": str(owner_id), "content": "kept"},
    )
    await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth_headers(owner_id))

    resp = await client.get(f"/api/v1/documents/{doc['id']}/comments")
    assert resp.status_code == 200
    assert [c["content"] for c in resp.json()] == ["kept"]
