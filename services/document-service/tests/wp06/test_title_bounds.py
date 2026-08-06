"""Title length / content boundary coverage.

The cap is 500: ``Field(..., min_length=1, max_length=500)`` on the schemas and
``String(500)`` on the model column.
"""

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from tests.wp06.conftest import MAX_TITLE_LENGTH, auth_headers

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("length", [1, MAX_TITLE_LENGTH - 1, MAX_TITLE_LENGTH])
async def test_create_accepts_title_up_to_max(
    client: AsyncClient, owner_id: uuid.UUID, length: int
):
    title = "t" * length
    resp = await client.post(
        "/api/v1/documents/", json={"title": title, "content": "x", "owner_id": str(owner_id)}
    )
    assert resp.status_code == 201
    assert len(resp.json()["title"]) == length


async def test_create_rejects_title_above_max(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        "/api/v1/documents/",
        json={
            "title": "t" * (MAX_TITLE_LENGTH + 1),
            "content": "x",
            "owner_id": str(owner_id),
        },
    )
    assert resp.status_code == 422


async def test_create_rejects_empty_title(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        "/api/v1/documents/", json={"title": "", "content": "x", "owner_id": str(owner_id)}
    )
    assert resp.status_code == 422


async def test_create_rejects_missing_title(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        "/api/v1/documents/", json={"content": "x", "owner_id": str(owner_id)}
    )
    assert resp.status_code == 422


async def test_create_rejects_null_title(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        "/api/v1/documents/", json={"title": None, "content": "x", "owner_id": str(owner_id)}
    )
    assert resp.status_code == 422


async def test_create_preserves_unicode_title_and_content(
    client: AsyncClient, owner_id: uuid.UUID, jwt_secret: str
):
    title = "Отчёт 🦦 — 文書 café"
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": title, "content": "naïve 🦦 text", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]
    assert resp.json()["title"] == title

    read = await client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers(owner_id))
    assert read.status_code == 200
    assert read.json()["title"] == title
    assert read.json()["content"] == "naïve 🦦 text"


async def test_unicode_title_counted_by_codepoints_not_bytes(
    client: AsyncClient, owner_id: uuid.UUID
):
    """A 500-codepoint emoji title is accepted even though it is >500 bytes."""
    title = "🦦" * MAX_TITLE_LENGTH
    resp = await client.post(
        "/api/v1/documents/", json={"title": title, "content": "", "owner_id": str(owner_id)}
    )
    assert resp.status_code == 201
    assert len(resp.json()["title"]) == MAX_TITLE_LENGTH


async def test_word_count_of_empty_content_is_zero(
    client: AsyncClient, owner_id: uuid.UUID
):
    resp = await client.post(
        "/api/v1/documents/", json={"title": "empty", "content": "", "owner_id": str(owner_id)}
    )
    assert resp.status_code == 201
    assert resp.json()["word_count"] == 0


async def test_word_count_ignores_repeated_whitespace(
    client: AsyncClient, owner_id: uuid.UUID
):
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "ws", "content": "  a \t b \n\n c  ", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 201
    assert resp.json()["word_count"] == 3


# ---- the same trio on the update paths ----


@pytest.mark.parametrize("length", [MAX_TITLE_LENGTH - 1, MAX_TITLE_LENGTH])
async def test_put_accepts_title_up_to_max(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID, length: int
):
    doc = await create_document(owner_id)
    resp = await client.put(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "u" * length, "content": "updated"},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    assert len(resp.json()["title"]) == length


async def test_put_rejects_title_above_max(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    resp = await client.put(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "u" * (MAX_TITLE_LENGTH + 1), "content": "updated"},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("title", ["", None])
async def test_patch_rejects_empty_or_null_title(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID, title
):
    doc = await create_document(owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{doc['id']}",
        json={"title": title},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 422


async def test_patch_accepts_title_at_max(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "p" * MAX_TITLE_LENGTH},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    assert len(resp.json()["title"]) == MAX_TITLE_LENGTH


async def test_patch_rejects_title_above_max(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "p" * (MAX_TITLE_LENGTH + 1)},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 422
