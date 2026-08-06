"""Repeat-request behaviour: which routes are idempotent and which append state."""

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from tests.wp06.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_deleting_a_document_twice_returns_404_the_second_time(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    first = await client.delete(
        f"/api/v1/documents/{doc['id']}", headers=auth_headers(owner_id)
    )
    second = await client.delete(
        f"/api/v1/documents/{doc['id']}", headers=auth_headers(owner_id)
    )
    assert (first.status_code, second.status_code) == (204, 404)


async def test_repeated_identical_put_appends_a_version_each_time(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    """Pins current behaviour: PUT is not idempotent w.r.t. version history."""
    doc = await create_document(owner_id)
    body = {"title": "same", "content": "same body"}
    for _ in range(2):
        resp = await client.put(
            f"/api/v1/documents/{doc['id']}", json=body, headers=auth_headers(owner_id)
        )
        assert resp.status_code == 200

    versions = (
        await client.get(
            f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
        )
    ).json()
    assert [v["version_number"] for v in versions] == [1, 2, 3]


async def test_empty_patch_body_does_not_bump_the_version(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    doc = await create_document(owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{doc['id']}", json={}, headers=auth_headers(owner_id)
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 1

    versions = (
        await client.get(
            f"/api/v1/documents/{doc['id']}/versions", headers=auth_headers(owner_id)
        )
    ).json()
    assert len(versions) == 1


async def test_repeated_restore_of_the_same_version_keeps_appending(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
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
    target = versions[0]["id"]

    for expected_version in (3, 4):
        resp = await client.post(
            f"/api/v1/documents/{doc['id']}/versions/{target}/restore",
            headers=auth_headers(owner_id),
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == expected_version
        assert resp.json()["content"] == "a"


async def test_creating_the_same_document_twice_yields_two_documents(
    client: AsyncClient, create_document: Callable, owner_id: uuid.UUID
):
    """There is no idempotency key on create; identical payloads are distinct docs."""
    first = await create_document(owner_id, title="dup", content="dup")
    second = await create_document(owner_id, title="dup", content="dup")
    assert first["id"] != second["id"]

    listing = await client.get(
        "/api/v1/documents/", params={"owner_id": str(owner_id)}
    )
    assert listing.json()["total"] == 2
