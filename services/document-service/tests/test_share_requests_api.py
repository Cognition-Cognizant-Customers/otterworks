"""API tests for the share-request declination endpoint (BRD section 5)."""

import os
import uuid

import jwt
import pytest
from httpx import AsyncClient

from app.api import share_requests

TEST_JWT_SECRET = "test-jwt-secret-for-unit-tests-pad32"  # noqa: S105
os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)


def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = jwt.encode({"user_id": str(user_id)}, TEST_JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def document(client: AsyncClient, owner_id: uuid.UUID) -> dict:
    """A document owned by ``owner_id`` — the subject of the share requests."""
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Shareable", "content": "Body", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture
def published(monkeypatch) -> list[tuple[str, dict]]:
    """Capture declination notices instead of publishing them to SNS."""
    events: list[tuple[str, dict]] = []

    async def _capture(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    monkeypatch.setattr(share_requests.event_publisher, "publish", _capture)
    return events


def payload(document: dict, **overrides) -> dict:
    body = {
        "document_id": document["id"],
        "source": "CLIENT_PORTAL",
        "region": "MA",
        "workspace_type": "HOME_DRIVE",
        "share_type": "PUBLIC_LINK",
        "transaction": "NEW_SHARE",
        "trust_score": 500,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_criteria_met_in_client_portal_declines_and_sends_notice(
    client: AsyncClient, owner_id: uuid.UUID, document: dict, published
):
    """BRD 5.1 — declined in the portal, declination notice generated."""
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(document, trust_score=545),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == "DECLINED"
    assert data["rule_id"] == "RULE-1"
    assert data["declination_notice_sent"] is True

    assert len(published) == 1
    event_type, event_payload = published[0]
    assert event_type == share_requests.DECLINATION_NOTICE_EVENT
    assert event_payload["rule_id"] == "RULE-1"
    assert event_payload["trust_score"] == 545


@pytest.mark.asyncio
async def test_criteria_not_met_in_client_portal_allows_and_sends_nothing(
    client: AsyncClient, owner_id: uuid.UUID, document: dict, published
):
    """BRD 5.2 — not declined, no declination notice."""
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(document, trust_score=590),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == "ALLOWED"
    assert data["rule_id"] is None
    assert data["declination_notice_sent"] is False
    assert published == []


@pytest.mark.asyncio
async def test_criteria_met_in_admin_console_blocks_without_notice(
    client: AsyncClient, owner_id: uuid.UUID, document: dict, published
):
    """BRD 5.3 — the share is blocked, no declination notice."""
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(
            document, source="ADMIN_CONSOLE", share_type="EXTERNAL_EMAIL", trust_score=579
        ),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == "BLOCKED"
    assert data["rule_id"] == "RULE-2"
    assert data["declination_notice_sent"] is False
    assert published == []


@pytest.mark.asyncio
async def test_criteria_not_met_in_admin_console_allows(
    client: AsyncClient, owner_id: uuid.UUID, document: dict, published
):
    """BRD 5.4 — the share is issued successfully, no declination notice."""
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(
            document, source="ADMIN_CONSOLE", share_type="EXTERNAL_EMAIL", trust_score=580
        ),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "ALLOWED"
    assert published == []


@pytest.mark.asyncio
async def test_requester_details_resolve_the_trust_score(
    client: AsyncClient, owner_id: uuid.UUID, document: dict, published
):
    """BRD 4 — designated test data reproduces the intended score band."""
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(
            document,
            trust_score=None,
            requester={
                "first_name": "Olive",
                "last_name": "Otter",
                "date_of_birth": "1985-03-14",
                "address": "12 Harbor St, Boston, MA",
            },
        ),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["trust_score"] == 545
    assert data["outcome"] == "DECLINED"


@pytest.mark.asyncio
async def test_explicit_trust_score_overrides_requester_details(
    client: AsyncClient, owner_id: uuid.UUID, document: dict, published
):
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(
            document,
            trust_score=700,
            requester={
                "first_name": "Olive",
                "last_name": "Otter",
                "date_of_birth": "1985-03-14",
                "address": "12 Harbor St, Boston, MA",
            },
        ),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    assert resp.json()["trust_score"] == 700
    assert resp.json()["outcome"] == "ALLOWED"


@pytest.mark.asyncio
async def test_missing_score_and_requester_is_rejected(
    client: AsyncClient, owner_id: uuid.UUID, document: dict
):
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(document, trust_score=None),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_no_trailing_slash_route(
    client: AsyncClient, owner_id: uuid.UUID, document: dict, published
):
    resp = await client.post(
        "/api/v1/share-requests",
        json=payload(document, trust_score=545),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "DECLINED"


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(
    client: AsyncClient, document: dict, published
):
    resp = await client.post("/api/v1/share-requests/", json=payload(document))
    assert resp.status_code == 401
    assert published == []


@pytest.mark.asyncio
async def test_non_owner_cannot_request_a_decision(
    client: AsyncClient, document: dict, published
):
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(document),
        headers=auth_headers(uuid.uuid4()),
    )
    assert resp.status_code == 403
    assert published == []


@pytest.mark.asyncio
async def test_unknown_document_is_not_found(
    client: AsyncClient, owner_id: uuid.UUID, document: dict
):
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(document, document_id=str(uuid.uuid4())),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"source": "FAX"},
        {"share_type": "CARRIER_PIGEON"},
        {"transaction": "AMENDMENT"},
        {"workspace_type": "ARCHIVE"},
        {"trust_score": 42},
        {"requester": {"first_name": "Olive"}},
    ],
)
async def test_invalid_input_is_rejected(
    client: AsyncClient, owner_id: uuid.UUID, document: dict, override
):
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(document, **override),
        headers=auth_headers(owner_id),
    )
    assert resp.status_code == 422
