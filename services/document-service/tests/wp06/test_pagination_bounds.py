"""Pagination boundary coverage for the list and search endpoints.

The real caps live on the route signatures in ``app/api/documents.py``:
``page = Query(1, ge=1)`` and ``size = Query(20, ge=1, le=100)``.
"""

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from tests.wp06.conftest import MAX_PAGE_SIZE

pytestmark = pytest.mark.asyncio


async def _seed(create_document: Callable, owner: uuid.UUID, count: int) -> None:
    for i in range(count):
        await create_document(owner, title=f"Doc {i:02d}")


# ---- page boundary: limit-1 / limit / limit+1 around the ge=1 floor ----


@pytest.mark.parametrize("page", [0, -1])
async def test_list_rejects_page_below_floor(
    client: AsyncClient, owner_id: uuid.UUID, page: int
):
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "page": page})
    assert resp.status_code == 422


async def test_list_accepts_page_at_floor(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    await _seed(create_document, owner_id, 3)
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "page": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["total"] == 3
    assert len(body["items"]) == 3


async def test_list_page_beyond_last_returns_empty_page(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    await _seed(create_document, owner_id, 3)
    resp = await client.get(
        "/api/v1/documents/", params={"owner_id": str(owner_id), "page": 3, "size": 2}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 3
    assert body["pages"] == 2
    assert body["page"] == 3


async def test_list_last_page_is_partial(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    await _seed(create_document, owner_id, 3)
    resp = await client.get(
        "/api/v1/documents/", params={"owner_id": str(owner_id), "page": 2, "size": 2}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["pages"] == 2


@pytest.mark.parametrize("page", ["abc", "1.5", "", "1,2"])
async def test_list_rejects_non_integer_page(
    client: AsyncClient, owner_id: uuid.UUID, page: str
):
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "page": page})
    assert resp.status_code == 422


# ---- size boundary: 0 / 1 / max-1 / max / max+1 ----


async def test_list_rejects_size_zero(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "size": 0})
    assert resp.status_code == 422


async def test_list_accepts_size_one(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    await _seed(create_document, owner_id, 3)
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "size": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["pages"] == 3


@pytest.mark.parametrize("size", [MAX_PAGE_SIZE - 1, MAX_PAGE_SIZE])
async def test_list_accepts_size_up_to_cap(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID, size: int
):
    await _seed(create_document, owner_id, 2)
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "size": size})
    assert resp.status_code == 200
    assert resp.json()["size"] == size


async def test_list_rejects_size_above_cap(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.get(
        "/api/v1/documents/", params={"owner_id": str(owner_id), "size": MAX_PAGE_SIZE + 1}
    )
    assert resp.status_code == 422


async def test_list_pagination_windows_do_not_overlap(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    await _seed(create_document, owner_id, 5)
    seen: list[str] = []
    for page in (1, 2, 3):
        resp = await client.get(
            "/api/v1/documents/",
            params={"owner_id": str(owner_id), "page": page, "size": 2},
        )
        assert resp.status_code == 200
        seen.extend(item["id"] for item in resp.json()["items"])
    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_list_no_slash_alias_applies_same_bounds(
    client: AsyncClient, owner_id: uuid.UUID
):
    resp = await client.get("/api/v1/documents", params={"owner_id": str(owner_id), "size": 0})
    assert resp.status_code == 422


# ---- the search route carries the same caps ----


async def test_search_rejects_empty_query(client: AsyncClient):
    resp = await client.get("/api/v1/documents/search", params={"q": ""})
    assert resp.status_code == 422


async def test_search_requires_query(client: AsyncClient):
    resp = await client.get("/api/v1/documents/search")
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"q": "Doc", "page": 0}, 422),
        ({"q": "Doc", "size": 0}, 422),
        ({"q": "Doc", "size": MAX_PAGE_SIZE + 1}, 422),
        ({"q": "Doc", "size": MAX_PAGE_SIZE}, 200),
        ({"q": "Doc", "page": 1, "size": 1}, 200),
    ],
)
async def test_search_pagination_bounds(
    client: AsyncClient,
    create_document: Callable,
    owner_id: uuid.UUID,
    params: dict,
    expected: int,
):
    await _seed(create_document, owner_id, 2)
    resp = await client.get("/api/v1/documents/search", params=params)
    assert resp.status_code == expected


async def test_search_page_beyond_last_is_empty(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    await _seed(create_document, owner_id, 2)
    resp = await client.get(
        "/api/v1/documents/search", params={"q": "Doc", "page": 5, "size": 1}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 2
