"""Shared helpers for the WP-06 coverage-expansion tests.

The filename deliberately does not match ``test_*`` so pytest does not collect it.
Everything here is pure/stateless: no module-level mutable state, so importing it
from several test modules cannot create ordering dependencies.
"""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest

# 53 bytes: above the 48-byte minimum PyJWT recommends for HS384, so signing and
# verifying with either supported algorithm is warning-free.
WP06_JWT_SECRET = "wp06-document-service-jwt-secret-key-0123456789abcdef"

# ``app.api.documents._extract_user_id`` accepts HS256 and HS384.
SUPPORTED_ALGORITHMS = ("HS256", "HS384")


@pytest.fixture(autouse=True)
def wp06_jwt_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin ``JWT_SECRET`` for the duration of one test.

    ``app.api.documents._get_jwt_secret`` reads ``os.environ`` on every call, and
    other test modules set the variable at import time. Setting it explicitly per
    test (and letting monkeypatch restore it) removes that ordering dependency.

    Autouse so that importing it into a module covers that module only; a test
    that needs the unconfigured-secret behaviour overrides it with its own
    ``monkeypatch.setenv``.
    """
    monkeypatch.setenv("JWT_SECRET", WP06_JWT_SECRET)
    return WP06_JWT_SECRET


def make_token(
    user_id: uuid.UUID | str | None,
    *,
    algorithm: str = "HS256",
    secret: str = WP06_JWT_SECRET,
    claim: str = "user_id",
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Build a JWT the document service will accept (or deliberately reject)."""
    payload: dict[str, Any] = dict(extra_claims or {})
    if user_id is not None:
        payload[claim] = str(user_id)
    return jwt.encode(payload, secret, algorithm=algorithm)


def auth_headers(user_id: uuid.UUID | str, **kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id, **kwargs)}"}


async def create_document(
    client: Any,
    owner: uuid.UUID,
    *,
    title: str = "WP-06 document",
    content: str = "body",
    folder_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create a document through the API and return the response body."""
    body: dict[str, Any] = {
        "title": title,
        "content": content,
        "owner_id": str(owner),
    }
    if folder_id is not None:
        body["folder_id"] = str(folder_id)
    resp = await client.post("/api/v1/documents/", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()
