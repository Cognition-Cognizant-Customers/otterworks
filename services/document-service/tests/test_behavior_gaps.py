"""Tests for previously unasserted behaviors (authz, boundaries, malformed payloads,
state-transition semantics). See PR description for the gap audit each test maps to."""

import os
import uuid

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.schemas.document import DocumentCreate
from app.services.document_service import DocumentService

TEST_JWT_SECRET = "test-jwt-secret-for-unit-tests-pad32"  # noqa: S105
os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)


def _make_jwt(user_id: str, secret: str = TEST_JWT_SECRET) -> str:
    return jwt.encode({"user_id": user_id}, secret, algorithm="HS256")


def _auth(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_jwt(str(user_id))}"}


async def _create_doc(
    client: AsyncClient, owner_id: uuid.UUID, title: str = "Doc", content: str = "Body"
) -> str:
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": title, "content": content, "owner_id": str(owner_id)},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ---- Authentication: 401 without credentials ----


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/documents/{id}", None),
        ("PUT", "/api/v1/documents/{id}", {"title": "X", "content": "Y"}),
        ("PATCH", "/api/v1/documents/{id}", {"title": "X"}),
        ("DELETE", "/api/v1/documents/{id}", None),
        ("GET", "/api/v1/documents/{id}/versions", None),
        ("GET", "/api/v1/documents/{id}/export", None),
    ],
)
async def test_document_endpoints_require_auth(
    client: AsyncClient, owner_id: uuid.UUID, method: str, path: str, body: dict | None
):
    doc_id = await _create_doc(client, owner_id)
    url = path.format(id=doc_id)
    resp = await client.request(method, url, json=body)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_restore_requires_auth(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id)
    versions = await client.get(f"/api/v1/documents/{doc_id}/versions", headers=_auth(owner_id))
    v1_id = versions.json()[0]["id"]
    resp = await client.post(f"/api/v1/documents/{doc_id}/versions/{v1_id}/restore")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jwt_with_wrong_secret_rejected(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id)
    forged = _make_jwt(str(owner_id), secret="attacker-secret-that-is-not-ours")  # noqa: S106
    resp = await client.get(
        f"/api/v1/documents/{doc_id}", headers={"Authorization": f"Bearer {forged}"}
    )
    assert resp.status_code == 401


# ---- Permission denied: authenticated non-owner gets 403 ----


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/documents/{id}", None),
        ("PUT", "/api/v1/documents/{id}", {"title": "Hijack", "content": "X"}),
        ("PATCH", "/api/v1/documents/{id}", {"title": "Hijack"}),
        ("DELETE", "/api/v1/documents/{id}", None),
        ("GET", "/api/v1/documents/{id}/versions", None),
        ("GET", "/api/v1/documents/{id}/export", None),
    ],
)
async def test_cross_user_access_forbidden(
    client: AsyncClient, owner_id: uuid.UUID, method: str, path: str, body: dict | None
):
    doc_id = await _create_doc(client, owner_id)
    attacker = uuid.uuid4()
    resp = await client.request(method, path.format(id=doc_id), json=body, headers=_auth(attacker))
    assert resp.status_code == 403

    # The document must be unchanged for its owner afterwards.
    check = await client.get(f"/api/v1/documents/{doc_id}", headers=_auth(owner_id))
    assert check.status_code == 200
    assert check.json()["title"] == "Doc"
    assert check.json()["version"] == 1


@pytest.mark.asyncio
async def test_cross_user_restore_forbidden(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id)
    versions = await client.get(f"/api/v1/documents/{doc_id}/versions", headers=_auth(owner_id))
    v1_id = versions.json()[0]["id"]
    resp = await client.post(
        f"/api/v1/documents/{doc_id}/versions/{v1_id}/restore", headers=_auth(uuid.uuid4())
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_defaults_to_authenticated_user(client: AsyncClient, owner_id: uuid.UUID):
    other = uuid.uuid4()
    await _create_doc(client, owner_id, title="Mine")
    await _create_doc(client, other, title="Theirs")

    resp = await client.get("/api/v1/documents/", headers=_auth(owner_id))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Mine"
    assert data["items"][0]["owner_id"] == str(owner_id)


# ---- Empty results and pagination boundaries ----


@pytest.mark.asyncio
async def test_list_empty_result(client: AsyncClient):
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(uuid.uuid4())})
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["pages"] == 1


@pytest.mark.asyncio
async def test_search_empty_result(client: AsyncClient, owner_id: uuid.UUID):
    await _create_doc(client, owner_id, title="Python Guide", content="Learn Python")
    resp = await client.get("/api/v1/documents/search", params={"q": "nonexistent-term"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["pages"] == 1


@pytest.mark.asyncio
async def test_list_page_beyond_last(client: AsyncClient, owner_id: uuid.UUID):
    for i in range(3):
        await _create_doc(client, owner_id, title=f"Doc {i}")
    resp = await client.get(
        "/api/v1/documents/",
        params={"owner_id": str(owner_id), "page": 5, "size": 2},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 3
    assert data["pages"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"size": 0},
        {"size": 101},
    ],
)
async def test_list_rejects_out_of_range_pagination(client: AsyncClient, params: dict):
    resp = await client.get("/api/v1/documents/", params=params)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_rejects_empty_query(client: AsyncClient):
    resp = await client.get("/api/v1/documents/search", params={"q": ""})
    assert resp.status_code == 422


# ---- Malformed payloads ----


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"content": "no title"},
        {"title": "", "content": "empty title"},
        {"title": "x" * 501, "content": "too long"},
        {"title": "T", "owner_id": "not-a-uuid"},
    ],
)
async def test_create_document_malformed_payload(client: AsyncClient, payload: dict):
    resp = await client.post("/api/v1/documents/", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"title": None},
        {"content": None},
        {"content_type": None},
    ],
)
async def test_patch_rejects_explicit_null(
    client: AsyncClient, owner_id: uuid.UUID, payload: dict
):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}", json=payload, headers=_auth(owner_id)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_add_comment_rejects_empty_content(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.post(
        f"/api/v1/documents/{doc_id}/comments",
        json={"author_id": str(uuid.uuid4()), "content": ""},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_export_rejects_unknown_format(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.get(
        f"/api/v1/documents/{doc_id}/export",
        params={"format": "docx"},
        headers=_auth(owner_id),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_uuid_path_rejected(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.get("/api/v1/documents/not-a-uuid", headers=_auth(owner_id))
    assert resp.status_code == 422


# ---- State-transition semantics ----


@pytest.mark.asyncio
async def test_patch_empty_body_does_not_bump_version(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.patch(f"/api/v1/documents/{doc_id}", json={}, headers=_auth(owner_id))
    assert resp.status_code == 200
    assert resp.json()["version"] == 1

    versions = await client.get(f"/api/v1/documents/{doc_id}/versions", headers=_auth(owner_id))
    assert len(versions.json()) == 1


@pytest.mark.asyncio
async def test_patch_empty_content_resets_word_count(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_id = await _create_doc(client, owner_id, content="three word body")
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}", json={"content": ""}, headers=_auth(owner_id)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["word_count"] == 0
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_update_recomputes_word_count(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id, content="one two")
    resp = await client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Doc", "content": "one two three four"},
        headers=_auth(owner_id),
    )
    assert resp.status_code == 200
    assert resp.json()["word_count"] == 4


@pytest.mark.asyncio
async def test_restore_recomputes_word_count(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id, content="one two three")
    await client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Doc", "content": "one"},
        headers=_auth(owner_id),
    )
    versions = await client.get(f"/api/v1/documents/{doc_id}/versions", headers=_auth(owner_id))
    v1_id = versions.json()[-1]["id"]  # oldest version (list is newest-first)

    resp = await client.post(
        f"/api/v1/documents/{doc_id}/versions/{v1_id}/restore", headers=_auth(owner_id)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "one two three"
    assert data["word_count"] == 3


@pytest.mark.asyncio
async def test_restore_rejects_version_of_other_document(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_a = await _create_doc(client, owner_id, title="A")
    doc_b = await _create_doc(client, owner_id, title="B")
    versions_b = await client.get(f"/api/v1/documents/{doc_b}/versions", headers=_auth(owner_id))
    foreign_version_id = versions_b.json()[0]["id"]

    resp = await client.post(
        f"/api/v1/documents/{doc_a}/versions/{foreign_version_id}/restore",
        headers=_auth(owner_id),
    )
    assert resp.status_code == 404

    check = await client.get(f"/api/v1/documents/{doc_a}", headers=_auth(owner_id))
    assert check.json()["title"] == "A"
    assert check.json()["version"] == 1


@pytest.mark.asyncio
async def test_delete_already_deleted_returns_404(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.delete(f"/api/v1/documents/{doc_id}", headers=_auth(owner_id))
    assert resp.status_code == 204
    resp = await client.delete(f"/api/v1/documents/{doc_id}", headers=_auth(owner_id))
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "body"),
    [
        ("PUT", {"title": "X", "content": "Y"}),
        ("PATCH", {"title": "X"}),
        ("DELETE", None),
    ],
)
async def test_mutations_on_nonexistent_document_return_404(
    client: AsyncClient, owner_id: uuid.UUID, method: str, body: dict | None
):
    resp = await client.request(
        method, f"/api/v1/documents/{uuid.uuid4()}", json=body, headers=_auth(owner_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deleted_document_excluded_from_list_and_search(
    client: AsyncClient, owner_id: uuid.UUID
):
    keep_id = await _create_doc(client, owner_id, title="Keep zebra", content="zebra")
    drop_id = await _create_doc(client, owner_id, title="Drop zebra", content="zebra")
    await client.delete(f"/api/v1/documents/{drop_id}", headers=_auth(owner_id))

    list_resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id)})
    ids = [item["id"] for item in list_resp.json()["items"]]
    assert keep_id in ids
    assert drop_id not in ids

    search_resp = await client.get("/api/v1/documents/search", params={"q": "zebra"})
    ids = [item["id"] for item in search_resp.json()["items"]]
    assert keep_id in ids
    assert drop_id not in ids


@pytest.mark.asyncio
async def test_templates_excluded_from_list_and_search(
    db_session: AsyncSession, owner_id: uuid.UUID
):
    service = DocumentService(db_session)
    await service.create(
        DocumentCreate(title="Regular quokka", content="quokka", owner_id=owner_id)
    )
    template_doc = Document(
        title="Template quokka",
        content="quokka",
        owner_id=owner_id,
        is_template=True,
        word_count=1,
        version=1,
    )
    db_session.add(template_doc)
    await db_session.commit()

    items, total = await service.list_documents(owner_id=owner_id)
    assert total == 1
    assert items[0].title == "Regular quokka"

    items, total = await service.search("quokka")
    assert total == 1
    assert items[0].title == "Regular quokka"


@pytest.mark.asyncio
async def test_html_export_escapes_markup(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        "/api/v1/documents/",
        json={
            "title": "<script>alert('t')</script>",
            "content": "<img src=x onerror=alert(1)>",
            "owner_id": str(owner_id),
        },
    )
    doc_id = resp.json()["id"]

    resp = await client.get(
        f"/api/v1/documents/{doc_id}/export",
        params={"format": "html"},
        headers=_auth(owner_id),
    )
    assert resp.status_code == 200
    assert "<script>alert" not in resp.text
    assert "<img" not in resp.text
    assert "&lt;script&gt;alert" in resp.text
    assert "&lt;img" in resp.text


@pytest.mark.asyncio
async def test_from_template_propagates_folder(
    client: AsyncClient, owner_id: uuid.UUID, folder_id: uuid.UUID
):
    template_resp = await client.post(
        "/api/v1/templates/",
        json={"name": "T", "content": "Template body", "created_by": str(uuid.uuid4())},
    )
    template_id = template_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/documents/from-template/{template_id}",
        json={"title": "In Folder", "owner_id": str(owner_id), "folder_id": str(folder_id)},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["folder_id"] == str(folder_id)
    assert data["word_count"] == 2  # word count computed from template content


@pytest.mark.asyncio
async def test_competing_updates_last_write_wins_with_full_history(
    client: AsyncClient, owner_id: uuid.UUID
):
    """Two editors race on the same document: last write wins, but every
    competing write is preserved as a version so nothing is silently lost."""
    doc_id = await _create_doc(client, owner_id, content="base")

    resp_a = await client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Doc", "content": "editor A"},
        headers=_auth(owner_id),
    )
    resp_b = await client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Doc", "content": "editor B"},
        headers=_auth(owner_id),
    )
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_b.json()["content"] == "editor B"
    assert resp_b.json()["version"] == 3

    versions = await client.get(f"/api/v1/documents/{doc_id}/versions", headers=_auth(owner_id))
    contents = [v["content"] for v in versions.json()]
    assert contents == ["editor B", "editor A", "base"]


@pytest.mark.asyncio
async def test_delete_comment_requires_matching_document(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_a = await _create_doc(client, owner_id, title="A")
    doc_b = await _create_doc(client, owner_id, title="B")
    comment_resp = await client.post(
        f"/api/v1/documents/{doc_a}/comments",
        json={"author_id": str(uuid.uuid4()), "content": "on A"},
    )
    comment_id = comment_resp.json()["id"]

    resp = await client.delete(f"/api/v1/documents/{doc_b}/comments/{comment_id}")
    assert resp.status_code == 404

    list_resp = await client.get(f"/api/v1/documents/{doc_a}/comments")
    assert len(list_resp.json()) == 1
