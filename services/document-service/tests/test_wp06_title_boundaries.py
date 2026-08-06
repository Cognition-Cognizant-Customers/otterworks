"""WP-06: maximum-length title/name boundaries on every route that accepts one.

``title`` is declared ``min_length=1, max_length=500`` on ``DocumentCreate``,
``DocumentUpdate``, ``DocumentPatch`` and ``DocumentFromTemplate``; ``name``
carries the same bounds on ``TemplateCreate``. Each is covered with the
boundary trio 499 / 500 / 501 plus the empty-string lower bound.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests._wp06_support import auth_headers, create_document, wp06_jwt_env  # noqa: F401

TITLE_MAX = 500


def _title(length: int, fill: str = "t") -> str:
    return fill * length


# ---------------------------------------------------------------- boundary --


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("length", "expected_status"),
    [(TITLE_MAX - 1, 201), (TITLE_MAX, 201), (TITLE_MAX + 1, 422)],
)
async def test_create_title_length_trio(
    client: AsyncClient, owner_id: uuid.UUID, length: int, expected_status: int
):
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": _title(length), "content": "", "owner_id": str(owner_id)},
    )
    assert resp.status_code == expected_status
    if expected_status == 201:
        assert len(resp.json()["title"]) == length


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("length", "expected_status"),
    [(TITLE_MAX - 1, 200), (TITLE_MAX, 200), (TITLE_MAX + 1, 422)],
)
async def test_put_title_length_trio(
    client: AsyncClient,
    owner_id: uuid.UUID,
    length: int,
    expected_status: int,
):
    document = await create_document(client, owner_id)
    resp = await client.put(
        f"/api/v1/documents/{document['id']}",
        json={"title": _title(length), "content": "body"},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("length", "expected_status"),
    [(TITLE_MAX - 1, 200), (TITLE_MAX, 200), (TITLE_MAX + 1, 422)],
)
async def test_patch_title_length_trio(
    client: AsyncClient,
    owner_id: uuid.UUID,
    length: int,
    expected_status: int,
):
    document = await create_document(client, owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{document['id']}",
        json={"title": _title(length)},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("length", "expected_status"),
    [(TITLE_MAX - 1, 201), (TITLE_MAX, 201), (TITLE_MAX + 1, 422)],
)
async def test_template_name_length_trio(
    client: AsyncClient, owner_id: uuid.UUID, length: int, expected_status: int
):
    resp = await client.post(
        "/api/v1/templates/",
        json={
            "name": _title(length),
            "content": "template body",
            "created_by": str(owner_id),
        },
    )
    assert resp.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("length", "expected_status"),
    [(TITLE_MAX - 1, 201), (TITLE_MAX, 201), (TITLE_MAX + 1, 422)],
)
async def test_create_from_template_title_length_trio(
    client: AsyncClient, owner_id: uuid.UUID, length: int, expected_status: int
):
    template = await client.post(
        "/api/v1/templates/",
        json={"name": "Base", "content": "seed", "created_by": str(owner_id)},
    )
    template_id = template.json()["id"]

    resp = await client.post(
        f"/api/v1/documents/from-template/{template_id}",
        json={"title": _title(length), "owner_id": str(owner_id)},
    )
    assert resp.status_code == expected_status


@pytest.mark.asyncio
async def test_maximum_length_title_survives_versioning_and_export(
    client: AsyncClient, owner_id: uuid.UUID
):
    at_cap = _title(TITLE_MAX)
    document = await create_document(client, owner_id, title=at_cap)

    versions = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner_id)
    )
    assert versions.status_code == 200
    assert versions.json()[0]["title"] == at_cap

    export = await client.get(
        f"/api/v1/documents/{document['id']}/export",
        params={"format": "markdown"},
        headers=auth_headers(owner_id),
    )
    assert export.status_code == 200
    assert export.text.startswith(f"# {at_cap}")


@pytest.mark.asyncio
async def test_a_title_at_the_cap_of_multibyte_characters_is_counted_in_characters(
    client: AsyncClient, owner_id: uuid.UUID
):
    """``max_length`` counts characters, not bytes: 500 emoji are accepted."""
    accepted = await client.post(
        "/api/v1/documents/",
        json={"title": _title(TITLE_MAX, "🦦"), "owner_id": str(owner_id)},
    )
    assert accepted.status_code == 201
    assert len(accepted.json()["title"]) == TITLE_MAX

    rejected = await client.post(
        "/api/v1/documents/",
        json={"title": _title(TITLE_MAX + 1, "🦦"), "owner_id": str(owner_id)},
    )
    assert rejected.status_code == 422


# ---------------------------------------------------------------- negative --


@pytest.mark.asyncio
async def test_create_rejects_an_empty_title(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post("/api/v1/documents/", json={"title": "", "owner_id": str(owner_id)})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_a_missing_title(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post("/api/v1/documents/", json={"owner_id": str(owner_id)})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_a_null_title(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post("/api/v1/documents/", json={"title": None, "owner_id": str(owner_id)})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_an_explicit_null_title(client: AsyncClient, owner_id: uuid.UUID):
    """``DocumentPatch`` distinguishes "absent" from "null" via a validator."""
    document = await create_document(client, owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{document['id']}",
        json={"title": None},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_an_empty_title(client: AsyncClient, owner_id: uuid.UUID):
    document = await create_document(client, owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{document['id']}",
        json={"title": ""},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_a_whitespace_only_title_is_currently_accepted(
    client: AsyncClient, owner_id: uuid.UUID
):
    """Pinned behaviour: ``min_length`` is applied to the raw string, unstripped.

    A single space therefore passes validation and is stored verbatim. Recorded
    so a future decision to strip titles shows up as a deliberate change.
    """
    resp = await client.post("/api/v1/documents/", json={"title": "   ", "owner_id": str(owner_id)})
    assert resp.status_code == 201
    assert resp.json()["title"] == "   "


@pytest.mark.asyncio
async def test_word_count_of_a_whitespace_only_body_is_zero(
    client: AsyncClient, owner_id: uuid.UUID
):
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "blank body", "content": "   \n\t ", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 201
    assert resp.json()["word_count"] == 0


@pytest.mark.asyncio
async def test_empty_content_is_allowed_and_defaults_to_empty_string(
    client: AsyncClient, owner_id: uuid.UUID
):
    resp = await client.post(
        "/api/v1/documents/", json={"title": "no body", "owner_id": str(owner_id)}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["content"] == ""
    assert body["word_count"] == 0
