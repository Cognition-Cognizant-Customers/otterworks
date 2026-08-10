"""Tests for document API endpoints."""

import uuid

import jwt
import pytest
from httpx import AsyncClient

from tests.conftest import TEST_JWT_SECRET
from tests.conftest import auth_header as _auth


@pytest.mark.asyncio
async def test_create_document(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Test Document", "content": "Hello world"},
        headers=_auth(owner_id),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Document"
    assert data["content"] == "Hello world"
    assert data["word_count"] == 2
    assert data["version"] == 1
    assert data["owner_id"] == str(owner_id)


@pytest.mark.asyncio
async def test_get_document(client: AsyncClient, owner_id: uuid.UUID):
    create_resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Doc", "content": "Body"},
        headers=_auth(owner_id),
    )
    doc_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/documents/{doc_id}", headers=_auth(owner_id))
    assert resp.status_code == 200
    assert resp.json()["id"] == doc_id


@pytest.mark.asyncio
async def test_get_document_not_found(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=_auth(owner_id))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_documents(client: AsyncClient, owner_id: uuid.UUID):
    for i in range(3):
        await client.post(
            "/api/v1/documents/",
            json={"title": f"Doc {i}", "content": ""},
            headers=_auth(owner_id),
        )
    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_list_documents_pagination(client: AsyncClient, owner_id: uuid.UUID):
    for i in range(5):
        await client.post(
            "/api/v1/documents/",
            json={"title": f"Doc {i}", "content": ""},
            headers=_auth(owner_id),
        )
    resp = await client.get(
        "/api/v1/documents/", params={"owner_id": str(owner_id), "page": 1, "size": 2}
    )
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["pages"] == 3


@pytest.mark.asyncio
async def test_update_document(client: AsyncClient, owner_id: uuid.UUID):
    create_resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Original", "content": "Old body"},
        headers=_auth(owner_id),
    )
    doc_id = create_resp.json()["id"]

    resp = await client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Updated", "content": "New body"},
        headers=_auth(owner_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated"
    assert data["content"] == "New body"
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_patch_document(client: AsyncClient, owner_id: uuid.UUID):
    create_resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Original", "content": "Body"},
        headers=_auth(owner_id),
    )
    doc_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Patched Title"},
        headers=_auth(owner_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Patched Title"
    assert data["content"] == "Body"  # unchanged
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_delete_document(client: AsyncClient, owner_id: uuid.UUID):
    create_resp = await client.post(
        "/api/v1/documents/",
        json={"title": "To Delete", "content": ""},
        headers=_auth(owner_id),
    )
    doc_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/v1/documents/{doc_id}", headers=_auth(owner_id))
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/documents/{doc_id}", headers=_auth(owner_id))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_document_versions(client: AsyncClient, owner_id: uuid.UUID):
    create_resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Versioned", "content": "v1"},
        headers=_auth(owner_id),
    )
    doc_id = create_resp.json()["id"]

    await client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Versioned", "content": "v2"},
        headers=_auth(owner_id),
    )

    resp = await client.get(f"/api/v1/documents/{doc_id}/versions", headers=_auth(owner_id))
    assert resp.status_code == 200
    versions = resp.json()
    assert [version["version_number"] for version in versions] == [1, 2]


@pytest.mark.asyncio
async def test_restore_version(client: AsyncClient, owner_id: uuid.UUID):
    create_resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Restore Me", "content": "Original"},
        headers=_auth(owner_id),
    )
    doc_id = create_resp.json()["id"]

    await client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Changed", "content": "Changed body"},
        headers=_auth(owner_id),
    )

    versions_resp = await client.get(
        f"/api/v1/documents/{doc_id}/versions", headers=_auth(owner_id)
    )
    v1_id = versions_resp.json()[0]["id"]  # first version

    resp = await client.post(
        f"/api/v1/documents/{doc_id}/versions/{v1_id}/restore", headers=_auth(owner_id)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Restore Me"
    assert data["content"] == "Original"
    assert data["version"] == 3


@pytest.mark.asyncio
async def test_search_documents(client: AsyncClient, owner_id: uuid.UUID):
    await client.post(
        "/api/v1/documents/",
        json={"title": "Python Guide", "content": "Learn Python"},
        headers=_auth(owner_id),
    )
    await client.post(
        "/api/v1/documents/",
        json={"title": "Rust Guide", "content": "Learn Rust"},
        headers=_auth(owner_id),
    )

    resp = await client.get("/api/v1/documents/search", params={"q": "Python"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Python Guide"


@pytest.mark.asyncio
async def test_export_document_html(client: AsyncClient, owner_id: uuid.UUID):
    create_resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Export", "content": "Content here"},
        headers=_auth(owner_id),
    )
    doc_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/v1/documents/{doc_id}/export",
        params={"format": "html"},
        headers=_auth(owner_id),
    )
    assert resp.status_code == 200
    assert "<h1>Export</h1>" in resp.text


@pytest.mark.asyncio
async def test_export_document_markdown(client: AsyncClient, owner_id: uuid.UUID):
    create_resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Export MD", "content": "MD content"},
        headers=_auth(owner_id),
    )
    doc_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/v1/documents/{doc_id}/export",
        params={"format": "markdown"},
        headers=_auth(owner_id),
    )
    assert resp.status_code == 200
    assert "# Export MD" in resp.text


@pytest.mark.asyncio
async def test_create_document_via_jwt(client: AsyncClient):
    """Create a document without owner_id in the body, using JWT instead."""
    user_id = uuid.uuid4()
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "JWT Doc", "content": "Created via JWT"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "JWT Doc"
    assert data["owner_id"] == str(user_id)


@pytest.mark.asyncio
async def test_create_document_via_jwt_hs384(client: AsyncClient):
    """Create a document using an HS384-signed JWT (matches auth-service algorithm)."""
    user_id = uuid.uuid4()
    token = jwt.encode({"sub": str(user_id)}, TEST_JWT_SECRET, algorithm="HS384")
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "HS384 Doc"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["owner_id"] == str(user_id)


@pytest.mark.asyncio
async def test_create_document_x_user_id_header_ignored(client: AsyncClient):
    """X-User-Id header alone is not trusted (prevents identity spoofing)."""
    user_id = uuid.uuid4()
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Header Doc"},
        headers={"X-User-Id": str(user_id)},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_document_no_auth_returns_401(client: AsyncClient):
    """Creating a document without auth returns 401, whatever the body claims."""
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "No Auth Doc", "owner_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_document_ignores_body_owner_id(client: AsyncClient):
    """A caller cannot name another account as owner (CWE-915 mass assignment)."""
    attacker_id = uuid.uuid4()
    victim_id = uuid.uuid4()
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Planted", "content": "body", "owner_id": str(victim_id)},
        headers=_auth(attacker_id),
    )
    assert resp.status_code == 201
    assert resp.json()["owner_id"] == str(attacker_id)


@pytest.mark.asyncio
async def test_create_from_template_ignores_body_owner_id(client: AsyncClient):
    """The same applies to instantiating a template."""
    attacker_id = uuid.uuid4()
    victim_id = uuid.uuid4()
    template_resp = await client.post(
        "/api/v1/templates/",
        json={
            "name": "Report",
            "description": "",
            "content": "template body",
            "created_by": str(attacker_id),
        },
    )
    template_id = template_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/documents/from-template/{template_id}",
        json={"title": "From Template", "owner_id": str(victim_id)},
        headers=_auth(attacker_id),
    )
    assert resp.status_code == 201
    assert resp.json()["owner_id"] == str(attacker_id)


@pytest.mark.asyncio
async def test_create_from_template_requires_auth(client: AsyncClient):
    attacker_id = uuid.uuid4()
    template_resp = await client.post(
        "/api/v1/templates/",
        json={
            "name": "Report",
            "description": "",
            "content": "template body",
            "created_by": str(attacker_id),
        },
    )
    template_id = template_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/documents/from-template/{template_id}",
        json={"title": "From Template", "owner_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401
