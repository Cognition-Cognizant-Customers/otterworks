"""WP-06: pagination boundary coverage for list, no-slash list and search.

The two paginated routes declare ``page: int = Query(1, ge=1)`` and
``size: int = Query(20, ge=1, le=100)``. Every one of those numeric limits is
covered here with the mandated boundary trio (limit-1 / limit / limit+1), plus
the page-past-the-end case and the ``paginate`` helper's own edges.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.services.document_service import DocumentService
from tests._wp06_support import auth_headers, create_document, wp06_jwt_env  # noqa: F401

LIST_ROUTES = ("/api/v1/documents/", "/api/v1/documents")

# Declared bounds, kept next to the tests that pin them.
PAGE_MIN = 1
SIZE_MIN = 1
SIZE_MAX = 100


async def _seed(client: AsyncClient, owner: uuid.UUID, count: int) -> None:
    for index in range(count):
        await create_document(client, owner, title=f"Doc {index:02d}", content="x")


# ---------------------------------------------------------------- boundary --


@pytest.mark.asyncio
@pytest.mark.parametrize("route", LIST_ROUTES)
@pytest.mark.parametrize(
    ("page", "expected_status"),
    [
        (PAGE_MIN - 2, 422),  # page=-1
        (PAGE_MIN - 1, 422),  # page=0
        (PAGE_MIN, 200),  # page=1, the lowest accepted value
        (PAGE_MIN + 1, 200),
    ],
)
async def test_list_page_lower_bound_trio(
    client: AsyncClient, owner_id: uuid.UUID, route: str, page: int, expected_status: int
):
    await _seed(client, owner_id, 3)
    resp = await client.get(route, params={"owner_id": str(owner_id), "page": page})
    assert resp.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "expected_status"),
    [
        (SIZE_MIN - 1, 422),  # size=0
        (SIZE_MIN, 200),
        (SIZE_MIN + 1, 200),
        (SIZE_MAX - 1, 200),  # 99
        (SIZE_MAX, 200),  # 100, the cap
        (SIZE_MAX + 1, 422),  # 101
    ],
)
async def test_list_size_cap_trio(
    client: AsyncClient, owner_id: uuid.UUID, size: int, expected_status: int
):
    await _seed(client, owner_id, 2)
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "size": size})
    assert resp.status_code == expected_status
    if expected_status == 200:
        body = resp.json()
        assert body["size"] == size
        assert len(body["items"]) <= size


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "expected_status"),
    [(SIZE_MAX - 1, 200), (SIZE_MAX, 200), (SIZE_MAX + 1, 422)],
)
async def test_search_size_cap_trio(
    client: AsyncClient, owner_id: uuid.UUID, size: int, expected_status: int
):
    await create_document(client, owner_id, title="Findable", content="needle")
    resp = await client.get("/api/v1/documents/search", params={"q": "needle", "size": size})
    assert resp.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [PAGE_MIN - 2, PAGE_MIN - 1])
async def test_search_rejects_non_positive_page(
    client: AsyncClient, owner_id: uuid.UUID, page: int
):
    await create_document(client, owner_id, content="needle")
    resp = await client.get("/api/v1/documents/search", params={"q": "needle", "page": page})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_one_page_past_the_last_is_empty_but_reports_the_true_total(
    client: AsyncClient, owner_id: uuid.UUID
):
    await _seed(client, owner_id, 5)
    last_page = 3  # ceil(5 / 2)

    on_last = await client.get(
        "/api/v1/documents/",
        params={"owner_id": str(owner_id), "page": last_page, "size": 2},
    )
    assert on_last.status_code == 200
    assert len(on_last.json()["items"]) == 1

    past_last = await client.get(
        "/api/v1/documents/",
        params={"owner_id": str(owner_id), "page": last_page + 1, "size": 2},
    )
    assert past_last.status_code == 200
    body = past_last.json()
    assert body["items"] == []
    assert body["total"] == 5
    assert body["pages"] == last_page
    assert body["page"] == last_page + 1


@pytest.mark.asyncio
async def test_list_far_past_the_last_page_is_still_empty_not_an_error(
    client: AsyncClient, owner_id: uuid.UUID
):
    await _seed(client, owner_id, 2)
    resp = await client.get(
        "/api/v1/documents/", params={"owner_id": str(owner_id), "page": 10_000}
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_search_one_page_past_the_last_is_empty(client: AsyncClient, owner_id: uuid.UUID):
    for index in range(3):
        await create_document(client, owner_id, title=f"needle {index}", content="body")

    resp = await client.get(
        "/api/v1/documents/search", params={"q": "needle", "page": 4, "size": 1}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 3
    assert body["pages"] == 3


@pytest.mark.asyncio
async def test_pages_is_one_when_there_is_nothing_to_page_through(
    client: AsyncClient, owner_id: uuid.UUID
):
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["pages"] == 1
    assert body["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("total", "size", "expected_pages"),
    [(3, 4, 1), (4, 4, 1), (5, 4, 2)],  # exact-multiple boundary trio
)
async def test_page_count_around_an_exact_multiple(
    client: AsyncClient, owner_id: uuid.UUID, total: int, size: int, expected_pages: int
):
    await _seed(client, owner_id, total)
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "size": size})
    assert resp.json()["pages"] == expected_pages


# ------------------------------------------------------------- pagination --
# ``DocumentService.paginate`` is the arithmetic behind ``pages``.


@pytest.mark.parametrize(
    ("total", "page", "size", "expected"),
    [
        (0, 1, 20, 1),  # never reports zero pages
        (1, 1, 20, 1),
        (19, 1, 20, 1),
        (20, 1, 20, 1),
        (21, 1, 20, 2),
        (100, 5, 100, 1),
        (0, 1, 0, 1),  # size=0 short-circuits instead of dividing by zero
        (10, 1, -1, 1),  # negative size takes the same guard
    ],
)
def test_paginate_arithmetic(total: int, page: int, size: int, expected: int):
    assert DocumentService.paginate(total, page, size) == expected


# ---------------------------------------------------------------- negative --


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["abc", "1.5", "", "1e2"])
async def test_list_rejects_non_integer_page(client: AsyncClient, owner_id: uuid.UUID, value: str):
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id), "page": value})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_rejects_a_malformed_owner_id(client: AsyncClient):
    resp = await client.get("/api/v1/documents/", params={"owner_id": "not-a-uuid"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_requires_a_non_empty_query(client: AsyncClient):
    """``q`` is ``min_length=1``; the 0/1-character pair is its boundary."""
    empty = await client.get("/api/v1/documents/search", params={"q": ""})
    assert empty.status_code == 422

    missing = await client.get("/api/v1/documents/search")
    assert missing.status_code == 422


@pytest.mark.asyncio
async def test_search_accepts_a_single_character_query(client: AsyncClient, owner_id: uuid.UUID):
    await create_document(client, owner_id, title="a", content="")
    resp = await client.get("/api/v1/documents/search", params={"q": "a"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_search_wildcards_are_escaped_not_interpreted(
    client: AsyncClient, owner_id: uuid.UUID
):
    """A literal ``%`` must not behave as a LIKE wildcard matching everything."""
    await create_document(client, owner_id, title="plain title", content="plain")
    resp = await client.get("/api/v1/documents/search", params={"q": "%"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_pagination_slices_without_overlap_or_gaps(client: AsyncClient, owner_id: uuid.UUID):
    await _seed(client, owner_id, 7)
    seen: list[str] = []
    for page in range(1, 4):
        resp = await client.get(
            "/api/v1/documents/",
            params={"owner_id": str(owner_id), "page": page, "size": 3},
        )
        seen.extend(item["id"] for item in resp.json()["items"])

    assert len(seen) == 7
    assert len(set(seen)) == 7


@pytest.mark.asyncio
async def test_listing_excludes_soft_deleted_documents_from_the_total(
    client: AsyncClient, owner_id: uuid.UUID
):
    await _seed(client, owner_id, 3)
    doomed = await create_document(client, owner_id, title="doomed")

    before = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id)})
    assert before.json()["total"] == 4

    deleted = await client.delete(
        f"/api/v1/documents/{doomed['id']}", headers=auth_headers(owner_id)
    )
    assert deleted.status_code == 204

    after = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id)})
    assert after.json()["total"] == 3
