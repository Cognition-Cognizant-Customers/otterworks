"""Coverage for app/services/event_publisher.py with a hand-rolled SNS fake.

No boto3 network client is ever constructed: ``boto3.client`` is monkeypatched, so
the suite needs neither LocalStack nor credentials.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.schemas.document import DocumentCreate
from app.services import event_publisher as publisher_module
from app.services.document_service import DocumentService
from app.services.event_publisher import EventPublisher, _UUIDEncoder

# asyncio_mode = "auto" (pyproject) collects the async tests in this module; the
# module mixes sync and async cases so it carries no module-level asyncio mark.


class FakeSNSClient:
    """Records publish() calls, optionally raising a configured error."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"MessageId": "fake-message-id"}


@pytest.fixture
def sns_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sns_enabled", True)
    monkeypatch.setattr(settings, "sns_topic_arn", "arn:aws:sns:us-east-1:000000000000:docs")


@pytest.fixture
def fake_sns(monkeypatch: pytest.MonkeyPatch) -> FakeSNSClient:
    client = FakeSNSClient()
    created: list[dict[str, Any]] = []

    def _factory(service_name: str, **kwargs: Any) -> FakeSNSClient:
        created.append({"service_name": service_name, **kwargs})
        return client

    monkeypatch.setattr(boto3, "client", _factory)
    client.constructor_kwargs = created  # type: ignore[attr-defined]
    return client


# ---- disabled / positive paths ----


async def test_publish_is_a_noop_when_sns_disabled(
    monkeypatch: pytest.MonkeyPatch, fake_sns: FakeSNSClient
):
    monkeypatch.setattr(settings, "sns_enabled", False)
    await EventPublisher().publish("document_created", {"id": uuid.uuid4()})
    assert fake_sns.calls == []


async def test_publish_sends_expected_envelope(
    sns_enabled: None, fake_sns: FakeSNSClient
):
    document_id = uuid.uuid4()
    await EventPublisher().publish("document_created", {"id": document_id, "title": "t"})

    assert len(fake_sns.calls) == 1
    call = fake_sns.calls[0]
    assert call["TopicArn"] == "arn:aws:sns:us-east-1:000000000000:docs"
    assert call["MessageAttributes"] == {
        "event_type": {"DataType": "String", "StringValue": "document_created"}
    }
    message = json.loads(call["Message"])
    assert message["event_type"] == "document_created"
    assert message["payload"] == {"id": str(document_id), "title": "t"}
    # timestamp is ISO-8601 and parseable; value itself is not asserted
    assert datetime.fromisoformat(message["timestamp"]).tzinfo is not None


async def test_publish_serialises_uuid_and_datetime_payloads(
    sns_enabled: None, fake_sns: FakeSNSClient
):
    moment = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    identifier = uuid.uuid4()
    await EventPublisher().publish("document_updated", {"id": identifier, "at": moment})

    payload = json.loads(fake_sns.calls[0]["Message"])["payload"]
    assert payload == {"id": str(identifier), "at": "2024-01-02T03:04:05+00:00"}


async def test_client_is_created_once_and_reused(
    sns_enabled: None, fake_sns: FakeSNSClient
):
    instance = EventPublisher()
    await instance.publish("a", {})
    await instance.publish("b", {})

    assert len(fake_sns.calls) == 2
    assert len(fake_sns.constructor_kwargs) == 1
    assert fake_sns.constructor_kwargs[0]["service_name"] == "sns"
    assert fake_sns.constructor_kwargs[0]["region_name"] == settings.aws_region
    assert "endpoint_url" not in fake_sns.constructor_kwargs[0]


async def test_endpoint_url_is_forwarded_when_configured(
    monkeypatch: pytest.MonkeyPatch, sns_enabled: None, fake_sns: FakeSNSClient
):
    monkeypatch.setattr(settings, "aws_endpoint_url", "http://localstack:4566")
    await EventPublisher().publish("document_created", {})
    assert fake_sns.constructor_kwargs[0]["endpoint_url"] == "http://localstack:4566"


# ---- failure paths (all swallowed by design) ----


async def test_sns_client_error_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, sns_enabled: None
):
    client = FakeSNSClient(error=RuntimeError("SNS unavailable"))
    monkeypatch.setattr(boto3, "client", lambda *a, **k: client)

    await EventPublisher().publish("document_created", {"id": uuid.uuid4()})

    assert len(client.calls) == 1


async def test_client_construction_error_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, sns_enabled: None
):
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("no credentials")

    monkeypatch.setattr(boto3, "client", _boom)
    await EventPublisher().publish("document_created", {})


async def test_malformed_payload_is_swallowed_and_never_reaches_sns(
    sns_enabled: None, fake_sns: FakeSNSClient
):
    """A non-JSON-serialisable payload raises inside publish() and is dropped."""
    await EventPublisher().publish("document_created", {"blob": object()})
    assert fake_sns.calls == []


def test_uuid_encoder_rejects_unsupported_types():
    with pytest.raises(TypeError):
        json.dumps({"blob": object()}, cls=_UUIDEncoder)


def test_uuid_encoder_handles_naive_datetime():
    encoded = json.loads(json.dumps({"at": datetime(2024, 1, 2, 3, 4, 5)}, cls=_UUIDEncoder))
    assert encoded == {"at": "2024-01-02T03:04:05"}


# ---- interaction with the caller's transaction ----


async def test_publish_failure_does_not_roll_back_the_callers_write(
    monkeypatch: pytest.MonkeyPatch,
    sns_enabled: None,
    db_session: AsyncSession,
    owner_id: uuid.UUID,
):
    """Pins the real behaviour: the write is committed, the event is silently lost."""
    client = FakeSNSClient(error=RuntimeError("SNS unavailable"))
    monkeypatch.setattr(boto3, "client", lambda *a, **k: client)
    monkeypatch.setattr(publisher_module.event_publisher, "_client", None)

    document = await DocumentService(db_session).create(
        DocumentCreate(title="survives", content="body", owner_id=owner_id)
    )

    rows = await db_session.execute(select(Document).where(Document.id == document.id))
    assert rows.scalar_one().title == "survives"
    assert len(client.calls) == 1


async def test_successful_publish_accompanies_a_document_create(
    monkeypatch: pytest.MonkeyPatch,
    sns_enabled: None,
    fake_sns: FakeSNSClient,
    db_session: AsyncSession,
    owner_id: uuid.UUID,
):
    monkeypatch.setattr(publisher_module.event_publisher, "_client", None)

    document = await DocumentService(db_session).create(
        DocumentCreate(title="published", content="body", owner_id=owner_id)
    )

    message = json.loads(fake_sns.calls[0]["Message"])
    assert message["event_type"] == "document_created"
    assert message["payload"]["id"] == str(document.id)
    assert message["payload"]["owner_id"] == str(owner_id)


def test_module_exposes_a_publisher_singleton():
    assert isinstance(publisher_module.event_publisher, EventPublisher)
