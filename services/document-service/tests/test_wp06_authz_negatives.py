"""WP-06: cross-user access negatives across every documented route.

The service has two classes of route:

* document routes, which call ``_require_user_id`` + ``_ensure_owner`` — those
  are swept below for 401 (no/blank/bad credential) and 403 (someone else's
  document);
* comment, template and collection routes, which have no authentication at all.
  Those are pinned as they behave today, each paired with a strict-xfail stating
  the behaviour a fix should produce. See the PR write-up: judged genuine gaps,
  deliberately not fixed here (this is a test-only package).
"""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
from httpx import AsyncClient

from tests._wp06_support import (  # noqa: F401
    WP06_JWT_SECRET,
    auth_headers,
    create_document,
    make_token,
    wp06_jwt_env,
)


@pytest.fixture
def other_user_id() -> uuid.UUID:
    return uuid.uuid4()


def _owned_routes(document_id: str, version_id: str) -> list[tuple[str, str, Any]]:
    """(method, path, json-body) for every route guarded by ``_ensure_owner``.

    Ordered so that the destructive route comes last: the positive control walks
    this list against one document.
    """
    base = f"/api/v1/documents/{document_id}"
    return [
        ("GET", base, None),
        ("GET", f"{base}/versions", None),
        ("GET", f"{base}/export", None),
        ("PUT", base, {"title": "Hijacked", "content": "by someone else"}),
        ("PATCH", base, {"title": "Hijacked"}),
        ("POST", f"{base}/versions/{version_id}/restore", None),
        ("DELETE", base, None),
    ]


async def _seed(client: AsyncClient, owner: uuid.UUID) -> tuple[str, str]:
    document = await create_document(client, owner, title="Private", content="secret")
    versions = await client.get(
        f"/api/v1/documents/{document['id']}/versions", headers=auth_headers(owner)
    )
    return document["id"], versions.json()[0]["id"]


async def _call(client: AsyncClient, method: str, path: str, body: Any, headers: dict[str, str]):
    return await client.request(method, path, json=body, headers=headers)


# ------------------------------------------------------- cross-user (403) --


@pytest.mark.asyncio
async def test_every_owned_route_rejects_another_users_token(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    document_id, version_id = await _seed(client, owner_id)

    for method, path, body in _owned_routes(document_id, version_id):
        resp = await _call(client, method, path, body, auth_headers(other_user_id))
        assert resp.status_code == 403, f"{method} {path} returned {resp.status_code}"


@pytest.mark.asyncio
async def test_a_rejected_cross_user_write_changes_nothing(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    document_id, _ = await _seed(client, owner_id)

    await client.put(
        f"/api/v1/documents/{document_id}",
        json={"title": "Hijacked", "content": "by someone else"},
        headers=auth_headers(other_user_id),
    )

    resp = await client.get(f"/api/v1/documents/{document_id}", headers=auth_headers(owner_id))
    body = resp.json()
    assert body["title"] == "Private"
    assert body["content"] == "secret"
    assert body["version"] == 1


@pytest.mark.asyncio
async def test_a_rejected_cross_user_delete_leaves_the_document_readable(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    document_id, _ = await _seed(client, owner_id)

    denied = await client.delete(
        f"/api/v1/documents/{document_id}", headers=auth_headers(other_user_id)
    )
    assert denied.status_code == 403

    still_there = await client.get(
        f"/api/v1/documents/{document_id}", headers=auth_headers(owner_id)
    )
    assert still_there.status_code == 200


@pytest.mark.asyncio
async def test_a_cross_user_restore_cannot_rewrite_history(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    document_id, version_id = await _seed(client, owner_id)

    denied = await client.post(
        f"/api/v1/documents/{document_id}/versions/{version_id}/restore",
        headers=auth_headers(other_user_id),
    )
    assert denied.status_code == 403

    versions = await client.get(
        f"/api/v1/documents/{document_id}/versions", headers=auth_headers(owner_id)
    )
    assert len(versions.json()) == 1


@pytest.mark.asyncio
async def test_the_owner_is_still_allowed_through_every_owned_route(
    client: AsyncClient, owner_id: uuid.UUID
):
    """The control case: the 403 sweep above is not just rejecting everything."""
    document_id, version_id = await _seed(client, owner_id)

    for method, path, body in _owned_routes(document_id, version_id):
        resp = await _call(client, method, path, body, auth_headers(owner_id))
        assert resp.status_code in (200, 204), f"{method} {path} -> {resp.status_code}"


# ------------------------------------------------------ missing/bad auth --


@pytest.mark.asyncio
async def test_every_owned_route_requires_a_credential(client: AsyncClient, owner_id: uuid.UUID):
    document_id, version_id = await _seed(client, owner_id)

    for method, path, body in _owned_routes(document_id, version_id):
        resp = await _call(client, method, path, body, {})
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "header_value"),
    [
        ("empty", ""),
        ("bearer with no token", "Bearer "),
        ("wrong scheme", "Basic dXNlcjpwYXNz"),
        ("token without the bearer prefix", "not-a-bearer-token"),
        ("garbage after bearer", "Bearer not.a.jwt"),
    ],
)
async def test_malformed_authorization_headers_are_401(
    client: AsyncClient,
    owner_id: uuid.UUID,
    label: str,
    header_value: str,
):
    document_id, _ = await _seed(client, owner_id)

    resp = await client.get(
        f"/api/v1/documents/{document_id}", headers={"Authorization": header_value}
    )
    assert resp.status_code == 401, label


@pytest.mark.asyncio
async def test_a_token_signed_with_the_wrong_secret_is_401(
    client: AsyncClient, owner_id: uuid.UUID
):
    document_id, _ = await _seed(client, owner_id)
    forged = make_token(owner_id, secret="a-different-secret-entirely-0123456789ab")

    resp = await client.get(
        f"/api/v1/documents/{document_id}",
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_an_unsigned_alg_none_token_is_401(client: AsyncClient, owner_id: uuid.UUID):
    document_id, _ = await _seed(client, owner_id)
    unsigned = jwt.encode({"user_id": str(owner_id)}, key="", algorithm="none")

    resp = await client.get(
        f"/api/v1/documents/{document_id}",
        headers={"Authorization": f"Bearer {unsigned}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_an_expired_token_is_401(client: AsyncClient, owner_id: uuid.UUID):
    document_id, _ = await _seed(client, owner_id)
    expired = make_token(owner_id, extra_claims={"exp": 1_000_000_000})

    resp = await client.get(
        f"/api/v1/documents/{document_id}",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_token_without_a_subject_claim_is_401(client: AsyncClient, owner_id: uuid.UUID):
    document_id, _ = await _seed(client, owner_id)
    anonymous = make_token(None, extra_claims={"role": "admin"})

    resp = await client.get(
        f"/api/v1/documents/{document_id}",
        headers={"Authorization": f"Bearer {anonymous}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_token_whose_subject_is_not_a_uuid_is_401(client: AsyncClient, owner_id: uuid.UUID):
    document_id, _ = await _seed(client, owner_id)
    malformed = make_token("not-a-uuid")

    resp = await client.get(
        f"/api/v1/documents/{document_id}",
        headers={"Authorization": f"Bearer {malformed}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_the_x_user_id_header_cannot_stand_in_for_a_token(
    client: AsyncClient, owner_id: uuid.UUID
):
    """Header spoofing: ``X-User-ID`` is only consulted when no JWT secret is
    configured, so with one configured it must not grant access."""
    document_id, _ = await _seed(client, owner_id)

    resp = await client.get(
        f"/api/v1/documents/{document_id}", headers={"X-User-ID": str(owner_id)}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_the_x_user_id_header_cannot_override_a_valid_token(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    document_id, _ = await _seed(client, owner_id)

    resp = await client.get(
        f"/api/v1/documents/{document_id}",
        headers={
            **auth_headers(other_user_id),
            "X-User-ID": str(owner_id),
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_hs384_token_is_accepted_like_hs256(client: AsyncClient, owner_id: uuid.UUID):
    document_id, _ = await _seed(client, owner_id)

    resp = await client.get(
        f"/api/v1/documents/{document_id}",
        headers=auth_headers(owner_id, algorithm="HS384"),
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_an_unknown_document_is_404_even_with_another_users_token(
    client: AsyncClient, other_user_id: uuid.UUID
):
    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}", headers=auth_headers(other_user_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_403_versus_404_discloses_that_a_document_exists(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    """Pinned behaviour: existence check runs before the ownership check, so a
    non-owner can distinguish "not yours" (403) from "no such document" (404).
    Low severity, but it is an information leak; recorded, not changed."""
    document_id, _ = await _seed(client, owner_id)
    headers = auth_headers(other_user_id)

    existing = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
    missing = await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=headers)

    assert existing.status_code == 403
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_export_rejects_an_unsupported_format_before_touching_the_document(
    client: AsyncClient, owner_id: uuid.UUID
):
    document_id, _ = await _seed(client, owner_id)

    resp = await client.get(
        f"/api/v1/documents/{document_id}/export",
        params={"format": "docx"},
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 422


# --------------------------------------- unauthenticated routes (pinned) --


@pytest.mark.asyncio
async def test_listing_another_users_documents_needs_no_credential(
    client: AsyncClient, owner_id: uuid.UUID
):
    """FINDING (genuine, unfixed): ``GET /api/v1/documents/`` trusts the
    ``owner_id`` query parameter, so anyone can enumerate anyone's titles."""
    await create_document(client, owner_id, title="Private plans")

    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id)})

    assert resp.status_code == 200
    assert [item["title"] for item in resp.json()["items"]] == ["Private plans"]


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "GET /api/v1/documents/ accepts an arbitrary owner_id with no "
        "credential. Reported by WP-06; not fixed here (test-only package)."
    ),
)
async def test_listing_another_users_documents_should_require_a_credential(
    client: AsyncClient, owner_id: uuid.UUID
):
    await create_document(client, owner_id, title="Private plans")

    resp = await client.get("/api/v1/documents/", params={"owner_id": str(owner_id)})

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_search_returns_other_users_documents(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    """FINDING (genuine, unfixed): ``/documents/search`` is unauthenticated and
    unscoped — it matches on content across every tenant."""
    await create_document(client, owner_id, title="Q3 layoffs", content="confidential")
    await create_document(client, other_user_id, title="Recipes", content="public")

    resp = await client.get("/api/v1/documents/search", params={"q": "confidential"})

    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "GET /api/v1/documents/search is unauthenticated and returns every "
        "owner's documents. Reported by WP-06; not fixed here."
    ),
)
async def test_search_should_not_expose_other_users_documents(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    await create_document(client, owner_id, title="Q3 layoffs", content="confidential")

    resp = await client.get(
        "/api/v1/documents/search",
        params={"q": "confidential"},
        headers=auth_headers(other_user_id),
    )

    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_a_document_can_be_created_on_behalf_of_another_user(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    """FINDING (genuine, unfixed): an ``owner_id`` in the body wins over the
    authenticated identity, so a caller can plant a document in someone else's
    library."""
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Planted", "owner_id": str(owner_id)},
        headers=auth_headers(other_user_id),
    )

    assert resp.status_code == 201
    assert resp.json()["owner_id"] == str(owner_id)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "POST /api/v1/documents/ lets the body's owner_id override the "
        "authenticated identity. Reported by WP-06; not fixed here."
    ),
)
async def test_creating_a_document_should_ignore_a_foreign_owner_id(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Planted", "owner_id": str(owner_id)},
        headers=auth_headers(other_user_id),
    )

    assert resp.json()["owner_id"] == str(other_user_id)


@pytest.mark.asyncio
async def test_comment_routes_accept_any_caller(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    """FINDING (genuine, unfixed): the whole comment router is unauthenticated —
    a stranger can read, write and delete comments on a private document."""
    document = await create_document(client, owner_id, title="Private")

    created = await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(other_user_id), "content": "I was never invited"},
    )
    listed = await client.get(f"/api/v1/documents/{document['id']}/comments")
    removed = await client.delete(
        f"/api/v1/documents/{document['id']}/comments/{created.json()['id']}"
    )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert removed.status_code == 204


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "The comment router has no authentication: any caller can read/write/"
        "delete comments on another user's document. Reported by WP-06; not "
        "fixed here."
    ),
)
async def test_comment_routes_should_require_a_credential(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    document = await create_document(client, owner_id, title="Private")

    resp = await client.post(
        f"/api/v1/documents/{document['id']}/comments",
        json={"author_id": str(other_user_id), "content": "I was never invited"},
    )

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_template_routes_accept_any_caller(client: AsyncClient, owner_id: uuid.UUID):
    """FINDING (genuine, unfixed): templates are a global, unauthenticated pool."""
    created = await client.post(
        "/api/v1/templates/",
        json={"name": "Shared", "content": "body", "created_by": str(owner_id)},
    )
    listed = await client.get("/api/v1/templates/")

    assert created.status_code == 201
    assert listed.status_code == 200
    assert [t["name"] for t in listed.json()] == ["Shared"]


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "GET/POST /api/v1/templates/ are unauthenticated. Reported by WP-06; " "not fixed here."
    ),
)
async def test_template_routes_should_require_a_credential(client: AsyncClient):
    resp = await client.get("/api/v1/templates/")

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_from_template_accepts_any_owner_without_a_credential(
    client: AsyncClient, owner_id: uuid.UUID, other_user_id: uuid.UUID
):
    """FINDING (genuine, unfixed): ``/documents/from-template/{id}`` never calls
    ``_require_user_id``, so ``owner_id`` in the body is taken on trust."""
    template = await client.post(
        "/api/v1/templates/",
        json={"name": "Base", "content": "seed", "created_by": str(other_user_id)},
    )

    resp = await client.post(
        f"/api/v1/documents/from-template/{template.json()['id']}",
        json={"title": "Planted from template", "owner_id": str(owner_id)},
    )

    assert resp.status_code == 201
    assert resp.json()["owner_id"] == str(owner_id)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "POST /api/v1/documents/from-template/{id} is unauthenticated. "
        "Reported by WP-06; not fixed here."
    ),
)
async def test_create_from_template_should_require_a_credential(
    client: AsyncClient, owner_id: uuid.UUID
):
    template = await client.post(
        "/api/v1/templates/",
        json={"name": "Base", "content": "seed", "created_by": str(owner_id)},
    )

    resp = await client.post(
        f"/api/v1/documents/from-template/{template.json()['id']}",
        json={"title": "Planted", "owner_id": str(owner_id)},
    )

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_from_an_unknown_template_is_404(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        f"/api/v1/documents/from-template/{uuid.uuid4()}",
        json={"title": "Nowhere", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_health_and_metrics_stay_open(client: AsyncClient):
    """Probes must not require a credential."""
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/metrics")).status_code == 200


# ------------------------------------------------ no-secret fallback mode --


@pytest.mark.asyncio
async def test_without_a_configured_secret_the_gateway_header_is_trusted(
    client: AsyncClient, owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
):
    """Pinned behaviour of the deployed configuration: when ``JWT_SECRET`` is
    unset the service falls back to ``X-User-ID``, but only alongside a Bearer
    header, and the token itself is never verified."""
    monkeypatch.setenv("JWT_SECRET", WP06_JWT_SECRET)
    document = await create_document(client, owner_id, title="Fallback")

    monkeypatch.setenv("JWT_SECRET", "")
    resp = await client.get(
        f"/api/v1/documents/{document['id']}",
        headers={"Authorization": "Bearer anything", "X-User-ID": str(owner_id)},
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_without_a_configured_secret_a_foreign_header_still_gets_403(
    client: AsyncClient,
    owner_id: uuid.UUID,
    other_user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("JWT_SECRET", WP06_JWT_SECRET)
    document = await create_document(client, owner_id, title="Fallback")

    monkeypatch.setenv("JWT_SECRET", "")
    resp = await client.get(
        f"/api/v1/documents/{document['id']}",
        headers={"Authorization": "Bearer anything", "X-User-ID": str(other_user_id)},
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_without_a_configured_secret_a_malformed_header_is_401(
    client: AsyncClient, owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("JWT_SECRET", WP06_JWT_SECRET)
    document = await create_document(client, owner_id, title="Fallback")

    monkeypatch.setenv("JWT_SECRET", "")
    resp = await client.get(
        f"/api/v1/documents/{document['id']}",
        headers={"Authorization": "Bearer anything", "X-User-ID": "not-a-uuid"},
    )

    assert resp.status_code == 401
