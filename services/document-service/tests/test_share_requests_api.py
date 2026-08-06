"""API tests for the share-request declination endpoint (BRD section 5)."""

import uuid

import pytest
from httpx import AsyncClient

from app.api import share_requests


@pytest.fixture
def published(monkeypatch) -> list[tuple[str, dict]]:
    """Capture declination notices instead of publishing them to SNS."""
    events: list[tuple[str, dict]] = []

    async def _capture(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    monkeypatch.setattr(share_requests.event_publisher, "publish", _capture)
    return events


def payload(**overrides) -> dict:
    body = {
        "document_id": str(uuid.uuid4()),
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
    client: AsyncClient, published
):
    """BRD 5.1 — declined in the portal, declination notice generated."""
    resp = await client.post("/api/v1/share-requests/", json=payload(trust_score=545))
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
    client: AsyncClient, published
):
    """BRD 5.2 — not declined, no declination notice."""
    resp = await client.post("/api/v1/share-requests/", json=payload(trust_score=590))
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == "ALLOWED"
    assert data["rule_id"] is None
    assert data["declination_notice_sent"] is False
    assert published == []


@pytest.mark.asyncio
async def test_criteria_met_in_admin_console_blocks_without_notice(
    client: AsyncClient, published
):
    """BRD 5.3 — the share is blocked, no declination notice."""
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(source="ADMIN_CONSOLE", share_type="EXTERNAL_EMAIL", trust_score=579),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == "BLOCKED"
    assert data["rule_id"] == "RULE-2"
    assert data["declination_notice_sent"] is False
    assert published == []


@pytest.mark.asyncio
async def test_criteria_not_met_in_admin_console_allows(client: AsyncClient, published):
    """BRD 5.4 — the share is issued successfully, no declination notice."""
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(source="ADMIN_CONSOLE", share_type="EXTERNAL_EMAIL", trust_score=580),
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "ALLOWED"
    assert published == []


@pytest.mark.asyncio
async def test_requester_details_resolve_the_trust_score(client: AsyncClient, published):
    """BRD 4 — designated test data reproduces the intended score band."""
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(
            trust_score=None,
            requester={
                "first_name": "Olive",
                "last_name": "Otter",
                "date_of_birth": "1985-03-14",
                "address": "12 Harbor St, Boston, MA",
            },
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["trust_score"] == 545
    assert data["outcome"] == "DECLINED"


@pytest.mark.asyncio
async def test_explicit_trust_score_overrides_requester_details(client: AsyncClient, published):
    resp = await client.post(
        "/api/v1/share-requests/",
        json=payload(
            trust_score=700,
            requester={
                "first_name": "Olive",
                "last_name": "Otter",
                "date_of_birth": "1985-03-14",
                "address": "12 Harbor St, Boston, MA",
            },
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["trust_score"] == 700
    assert resp.json()["outcome"] == "ALLOWED"


@pytest.mark.asyncio
async def test_missing_score_and_requester_is_rejected(client: AsyncClient):
    resp = await client.post("/api/v1/share-requests/", json=payload(trust_score=None))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_no_trailing_slash_route(client: AsyncClient, published):
    resp = await client.post("/api/v1/share-requests", json=payload(trust_score=545))
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "DECLINED"


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
async def test_invalid_input_is_rejected(client: AsyncClient, override):
    resp = await client.post("/api/v1/share-requests/", json=payload(**override))
    assert resp.status_code == 422
