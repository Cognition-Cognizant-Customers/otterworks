"""Cross-user authorization negatives on document, version and comment routes."""

import uuid
from collections.abc import Callable

import jwt as pyjwt
import pytest
from httpx import AsyncClient

from tests.wp06.conftest import auth_headers, make_jwt

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def doc_of_a(create_document: Callable, user_a: uuid.UUID) -> dict:
    return await create_document(user_a, title="A's document", content="a secret")


@pytest.fixture
async def version_of_a(client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID) -> dict:
    resp = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}/versions", headers=auth_headers(user_a)
    )
    assert resp.status_code == 200
    return resp.json()[0]


# ---- unauthenticated: every owner-scoped route answers 401 ----


async def test_unauthenticated_read_is_401(
    client: AsyncClient, doc_of_a: dict, jwt_secret: str
):
    assert (await client.get(f"/api/v1/documents/{doc_of_a['id']}")).status_code == 401


async def test_unauthenticated_update_is_401(client: AsyncClient, doc_of_a: dict):
    resp = await client.put(
        f"/api/v1/documents/{doc_of_a['id']}", json={"title": "x", "content": "y"}
    )
    assert resp.status_code == 401


async def test_unauthenticated_patch_is_401(client: AsyncClient, doc_of_a: dict):
    resp = await client.patch(f"/api/v1/documents/{doc_of_a['id']}", json={"title": "x"})
    assert resp.status_code == 401


async def test_unauthenticated_delete_is_401(client: AsyncClient, doc_of_a: dict):
    assert (await client.delete(f"/api/v1/documents/{doc_of_a['id']}")).status_code == 401


async def test_unauthenticated_versions_is_401(client: AsyncClient, doc_of_a: dict):
    resp = await client.get(f"/api/v1/documents/{doc_of_a['id']}/versions")
    assert resp.status_code == 401


async def test_unauthenticated_restore_is_401(
    client: AsyncClient, doc_of_a: dict, version_of_a: dict
):
    resp = await client.post(
        f"/api/v1/documents/{doc_of_a['id']}/versions/{version_of_a['id']}/restore"
    )
    assert resp.status_code == 401


async def test_unauthenticated_export_is_401(client: AsyncClient, doc_of_a: dict):
    resp = await client.get(f"/api/v1/documents/{doc_of_a['id']}/export")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": ""},
    ],
)
async def test_malformed_credentials_are_401(
    client: AsyncClient, doc_of_a: dict, header: dict
):
    resp = await client.get(f"/api/v1/documents/{doc_of_a['id']}", headers=header)
    assert resp.status_code == 401


async def test_token_signed_with_wrong_secret_is_401(
    client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID, jwt_secret: str
):
    forged = pyjwt.encode(
        {"user_id": str(user_a)},
        "a-different-but-equally-long-secret-key-0123456789",
        algorithm="HS256",
    )
    resp = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}", headers={"Authorization": f"Bearer {forged}"}
    )
    assert resp.status_code == 401


async def test_x_user_id_header_cannot_impersonate_when_jwt_secret_is_set(
    client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID, jwt_secret: str
):
    resp = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}", headers={"X-User-ID": str(user_a)}
    )
    assert resp.status_code == 401


# ---- authenticated as user B: 403 on user A's resources ----


async def test_user_b_cannot_read_user_a_document(
    client: AsyncClient, doc_of_a: dict, user_b: uuid.UUID
):
    resp = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}", headers=auth_headers(user_b)
    )
    assert resp.status_code == 403


async def test_user_b_cannot_replace_user_a_document(
    client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID, user_b: uuid.UUID
):
    resp = await client.put(
        f"/api/v1/documents/{doc_of_a['id']}",
        json={"title": "hijacked", "content": "hijacked"},
        headers=auth_headers(user_b),
    )
    assert resp.status_code == 403

    unchanged = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}", headers=auth_headers(user_a)
    )
    assert unchanged.json()["title"] == "A's document"


async def test_user_b_cannot_patch_user_a_document(
    client: AsyncClient, doc_of_a: dict, user_b: uuid.UUID
):
    resp = await client.patch(
        f"/api/v1/documents/{doc_of_a['id']}",
        json={"title": "hijacked"},
        headers=auth_headers(user_b),
    )
    assert resp.status_code == 403


async def test_user_b_cannot_delete_user_a_document(
    client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID, user_b: uuid.UUID
):
    resp = await client.delete(
        f"/api/v1/documents/{doc_of_a['id']}", headers=auth_headers(user_b)
    )
    assert resp.status_code == 403

    still_there = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}", headers=auth_headers(user_a)
    )
    assert still_there.status_code == 200


async def test_user_b_cannot_list_user_a_versions(
    client: AsyncClient, doc_of_a: dict, user_b: uuid.UUID
):
    resp = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}/versions", headers=auth_headers(user_b)
    )
    assert resp.status_code == 403


async def test_user_b_cannot_restore_user_a_version(
    client: AsyncClient, doc_of_a: dict, version_of_a: dict, user_b: uuid.UUID
):
    resp = await client.post(
        f"/api/v1/documents/{doc_of_a['id']}/versions/{version_of_a['id']}/restore",
        headers=auth_headers(user_b),
    )
    assert resp.status_code == 403


async def test_user_b_cannot_export_user_a_document(
    client: AsyncClient, doc_of_a: dict, user_b: uuid.UUID
):
    resp = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}/export",
        params={"format": "markdown"},
        headers=auth_headers(user_b),
    )
    assert resp.status_code == 403


async def test_missing_document_is_404_before_ownership_is_checked(
    client: AsyncClient, user_b: uuid.UUID, jwt_secret: str
):
    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}", headers=auth_headers(user_b)
    )
    assert resp.status_code == 404


async def test_owner_token_signed_with_hs384_is_accepted(
    client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID
):
    token = make_jwt(user_a, algorithm="HS384")
    resp = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


# ---- list scoping ----


async def test_list_defaults_to_the_authenticated_users_documents(
    client: AsyncClient,
    create_document: Callable,
    user_a: uuid.UUID,
    user_b: uuid.UUID,
    doc_of_a: dict,
):
    await create_document(user_b, title="B's document")
    resp = await client.get("/api/v1/documents/", headers=auth_headers(user_b))
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()["items"]]
    assert titles == ["B's document"]


async def test_list_without_credentials_returns_everything(
    client: AsyncClient, create_document: Callable, user_b: uuid.UUID, doc_of_a: dict
):
    """Pins current behaviour: the list route is unauthenticated and unscoped."""
    await create_document(user_b, title="B's document")
    resp = await client.get("/api/v1/documents/")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


# ---- routes with no ownership enforcement at all (documented defects) ----


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING WP-06-2: GET /api/v1/documents/ accepts an arbitrary owner_id query "
        "parameter with no authentication, so any caller can enumerate another user's "
        "documents (titles, content and ids) by guessing/knowing their user id."
    ),
)
async def test_user_b_should_not_be_able_to_list_user_a_documents_by_owner_id(
    client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID, user_b: uuid.UUID
):
    resp = await client.get(
        "/api/v1/documents/",
        params={"owner_id": str(user_a)},
        headers=auth_headers(user_b),
    )
    assert resp.status_code in (401, 403), resp.json()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING WP-06-3: POST /api/v1/documents/{id}/comments has no authentication or "
        "ownership check (app/api/comments.py), so any caller can comment on any document "
        "and can even forge the author_id in the body."
    ),
)
async def test_user_b_should_not_be_able_to_comment_on_user_a_document(
    client: AsyncClient, doc_of_a: dict, user_b: uuid.UUID
):
    resp = await client.post(
        f"/api/v1/documents/{doc_of_a['id']}/comments",
        json={"author_id": str(user_b), "content": "intruder"},
        headers=auth_headers(user_b),
    )
    assert resp.status_code in (401, 403), resp.json()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING WP-06-3: GET /api/v1/documents/{id}/comments is unauthenticated, so any "
        "caller can read the discussion on another user's document."
    ),
)
async def test_user_b_should_not_be_able_to_read_user_a_comments(
    client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID, user_b: uuid.UUID
):
    await client.post(
        f"/api/v1/documents/{doc_of_a['id']}/comments",
        json={"author_id": str(user_a), "content": "private note"},
    )
    resp = await client.get(
        f"/api/v1/documents/{doc_of_a['id']}/comments", headers=auth_headers(user_b)
    )
    assert resp.status_code in (401, 403), resp.json()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING WP-06-3: DELETE /api/v1/documents/{id}/comments/{cid} is unauthenticated "
        "and does not compare the caller against the comment author or the document owner, "
        "so any caller can delete anyone's comment."
    ),
)
async def test_user_b_should_not_be_able_to_delete_user_a_comment(
    client: AsyncClient, doc_of_a: dict, user_a: uuid.UUID, user_b: uuid.UUID
):
    comment = (
        await client.post(
            f"/api/v1/documents/{doc_of_a['id']}/comments",
            json={"author_id": str(user_a), "content": "mine"},
        )
    ).json()
    resp = await client.delete(
        f"/api/v1/documents/{doc_of_a['id']}/comments/{comment['id']}",
        headers=auth_headers(user_b),
    )
    assert resp.status_code in (401, 403), resp.status_code


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING WP-06-4: POST /api/v1/documents/from-template/{id} is unauthenticated and "
        "takes owner_id straight from the body, so a caller can create documents owned by "
        "another user."
    ),
)
async def test_document_from_template_should_require_authentication(
    client: AsyncClient, user_a: uuid.UUID, user_b: uuid.UUID
):
    template = (
        await client.post(
            "/api/v1/templates/",
            json={"name": "tpl", "content": "hello", "created_by": str(user_b)},
        )
    ).json()
    resp = await client.post(
        f"/api/v1/documents/from-template/{template['id']}",
        json={"title": "planted", "owner_id": str(user_a)},
    )
    assert resp.status_code in (401, 403), resp.json()
