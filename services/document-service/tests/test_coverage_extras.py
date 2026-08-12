"""Additional tests covering auth branches, chaos helpers, lifecycle, and events."""

import datetime
import json
import os
import uuid
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError

from app.api import documents as documents_api
from app.db.session import get_db
from app.main import app, lifespan
from app.schemas.document import DocumentPatch, DocumentUpdate
from app.services.document_service import DocumentService
from app.services.event_publisher import EventPublisher, _UUIDEncoder

TEST_JWT_SECRET = "test-jwt-secret-for-unit-tests-pad32"  # noqa: S105
os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)


def _make_jwt(user_id: str) -> str:
    return jwt.encode({"user_id": user_id}, TEST_JWT_SECRET, algorithm="HS256")


def _auth(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_jwt(str(user_id))}"}


async def _create_doc(client: AsyncClient, owner_id: uuid.UUID) -> str:
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Doc", "content": "Body", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ---- Auth branches on document endpoints ----


@pytest.mark.asyncio
async def test_get_document_requires_auth(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.get(f"/api/v1/documents/{doc_id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_document_wrong_owner_forbidden(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.get(f"/api/v1/documents/{doc_id}", headers=_auth(uuid.uuid4()))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_document_wrong_owner_forbidden(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "X", "content": "Y"},
        headers=_auth(uuid.uuid4()),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_document_not_found(client: AsyncClient):
    resp = await client.put(
        f"/api/v1/documents/{uuid.uuid4()}",
        json={"title": "X", "content": "Y"},
        headers=_auth(uuid.uuid4()),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_document_not_found(client: AsyncClient):
    resp = await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}",
        json={"title": "X"},
        headers=_auth(uuid.uuid4()),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_document_wrong_owner_forbidden(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"title": "X"},
        headers=_auth(uuid.uuid4()),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_document_not_found(client: AsyncClient):
    resp = await client.delete(
        f"/api/v1/documents/{uuid.uuid4()}", headers=_auth(uuid.uuid4())
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_wrong_owner_forbidden(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.delete(
        f"/api/v1/documents/{doc_id}", headers=_auth(uuid.uuid4())
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_versions_not_found(client: AsyncClient):
    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/versions", headers=_auth(uuid.uuid4())
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_restore_version_document_not_found(client: AsyncClient):
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/versions/{uuid.uuid4()}/restore",
        headers=_auth(uuid.uuid4()),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_restore_version_version_not_found(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.post(
        f"/api/v1/documents/{doc_id}/versions/{uuid.uuid4()}/restore",
        headers=_auth(owner_id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_document_not_found(client: AsyncClient):
    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/export", headers=_auth(uuid.uuid4())
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_document_requires_auth(
    client: AsyncClient, owner_id: uuid.UUID
):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.get(f"/api/v1/documents/{doc_id}/export")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_jwt_rejected(client: AsyncClient):
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Bad Token Doc"},
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_x_user_id_fallback_when_no_secret(client: AsyncClient, monkeypatch):
    """With no JWT_SECRET configured, gateway-forwarded X-User-ID is honoured."""
    monkeypatch.setenv("JWT_SECRET", "")
    user_id = uuid.uuid4()
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Forwarded Doc"},
        headers={
            "Authorization": "Bearer opaque-gateway-token",
            "X-User-ID": str(user_id),
        },
    )
    assert resp.status_code == 201
    assert resp.json()["owner_id"] == str(user_id)


@pytest.mark.asyncio
async def test_x_user_id_invalid_when_no_secret(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "")
    resp = await client.post(
        "/api/v1/documents/",
        json={"title": "Forwarded Doc"},
        headers={
            "Authorization": "Bearer opaque-gateway-token",
            "X-User-ID": "not-a-uuid",
        },
    )
    assert resp.status_code == 401


# ---- No-slash routes ----


@pytest.mark.asyncio
async def test_create_document_no_slash(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        "/api/v1/documents",
        json={"title": "No Slash", "content": "", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_documents_no_slash(client: AsyncClient, owner_id: uuid.UUID):
    await _create_doc(client, owner_id)
    resp = await client.get(
        "/api/v1/documents", params={"owner_id": str(owner_id)}
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_documents_owner_from_jwt(client: AsyncClient, owner_id: uuid.UUID):
    await _create_doc(client, owner_id)
    await _create_doc(client, uuid.uuid4())
    resp = await client.get("/api/v1/documents/", headers=_auth(owner_id))
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# ---- From-template endpoint ----


@pytest.mark.asyncio
async def test_create_from_template_api(client: AsyncClient, owner_id: uuid.UUID):
    tpl_resp = await client.post(
        "/api/v1/templates/",
        json={
            "name": "Tpl",
            "content": "Template body",
            "created_by": str(uuid.uuid4()),
        },
    )
    assert tpl_resp.status_code == 201
    tpl_id = tpl_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/documents/from-template/{tpl_id}",
        json={"title": "From Tpl", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 201
    assert resp.json()["content"] == "Template body"


@pytest.mark.asyncio
async def test_create_from_template_not_found(client: AsyncClient, owner_id: uuid.UUID):
    resp = await client.post(
        f"/api/v1/documents/from-template/{uuid.uuid4()}",
        json={"title": "Orphan", "owner_id": str(owner_id)},
    )
    assert resp.status_code == 404


# ---- Comments 404 branches ----


@pytest.mark.asyncio
async def test_add_comment_document_not_found(client: AsyncClient):
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/comments",
        json={"author_id": str(uuid.uuid4()), "content": "Hi"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_comment_not_found(client: AsyncClient, owner_id: uuid.UUID):
    doc_id = await _create_doc(client, owner_id)
    resp = await client.delete(
        f"/api/v1/documents/{doc_id}/comments/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


# ---- Chaos helpers ----


def test_get_redis_lazy_init(monkeypatch):
    monkeypatch.setattr(documents_api, "_redis_client", None)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6399")
    client = documents_api._get_redis()
    assert client is documents_api._get_redis()  # cached


def test_chaos_active_false_on_redis_error(monkeypatch):
    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(documents_api, "_get_redis", _boom)
    assert documents_api._chaos_active("chaos:document-service:slow_queries") is False


@pytest.mark.asyncio
async def test_maybe_inject_latency_when_flag_active(monkeypatch):
    monkeypatch.setattr(documents_api, "_chaos_active", lambda _key: True)
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(documents_api.asyncio, "sleep", _fake_sleep)
    await documents_api._maybe_inject_latency()
    assert len(slept) == 1
    assert 3.0 <= slept[0] <= 5.0


@pytest.mark.asyncio
async def test_maybe_inject_latency_noop_when_inactive(monkeypatch):
    monkeypatch.setattr(documents_api, "_chaos_active", lambda _key: False)
    await documents_api._maybe_inject_latency()


# ---- Health degraded branch ----


class _FailingSession:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_health_degraded_when_db_down(client: AsyncClient):
    async def _override():
        yield _FailingSession()

    app.dependency_overrides[get_db] = _override
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["database"] == "disconnected"


# ---- App lifespan ----


class _FakeEngine:
    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_lifespan_startup_shutdown(monkeypatch):
    import app.main as main_mod

    async def _fake_init_db() -> None:
        pass

    fake_engine = _FakeEngine()
    monkeypatch.setattr(main_mod, "init_db", _fake_init_db)
    monkeypatch.setattr(main_mod, "engine", fake_engine)
    monkeypatch.setattr(main_mod.settings, "otel_enabled", False)

    async with lifespan(FastAPI()):
        pass
    assert fake_engine.disposed is True


@pytest.mark.asyncio
async def test_lifespan_with_otel(monkeypatch):
    import app.main as main_mod

    async def _fake_init_db() -> None:
        pass

    monkeypatch.setattr(main_mod, "init_db", _fake_init_db)
    monkeypatch.setattr(main_mod, "engine", _FakeEngine())
    monkeypatch.setattr(main_mod.settings, "otel_enabled", True)

    async with lifespan(FastAPI()):
        pass


# ---- DB session helpers ----


@pytest.mark.asyncio
async def test_init_db_and_get_db(monkeypatch):
    import app.db.session as session_mod
    import tests.conftest as conftest_mod

    monkeypatch.setattr(session_mod, "engine", conftest_mod.engine)
    monkeypatch.setattr(
        session_mod, "async_session", conftest_mod.TestingSessionLocal
    )

    await session_mod.init_db()

    agen = session_mod.get_db()
    session = await agen.__anext__()
    assert session is not None
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()


# ---- Event publisher ----


def test_uuid_encoder_serializes_uuid_and_datetime():
    payload = {
        "id": uuid.uuid4(),
        "ts": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    }
    out = json.loads(json.dumps(payload, cls=_UUIDEncoder))
    assert out["id"] == str(payload["id"])
    assert out["ts"] == "2026-01-01T00:00:00+00:00"

    with pytest.raises(TypeError):
        json.dumps({"bad": object()}, cls=_UUIDEncoder)


@pytest.mark.asyncio
async def test_publish_skipped_when_sns_disabled(monkeypatch):
    from app.services import event_publisher as ep_mod

    monkeypatch.setattr(ep_mod.settings, "sns_enabled", False)
    publisher = EventPublisher()
    await publisher.publish("event", {"key": "value"})
    assert publisher._client is None  # client never created


@pytest.mark.asyncio
async def test_publish_sends_to_sns_when_enabled(monkeypatch):
    from app.services import event_publisher as ep_mod

    monkeypatch.setattr(ep_mod.settings, "sns_enabled", True)
    monkeypatch.setattr(ep_mod.settings, "sns_topic_arn", "arn:aws:sns:test:topic")
    publisher = EventPublisher()
    publisher._client = MagicMock()

    await publisher.publish("document_created", {"id": uuid.uuid4()})

    publisher._client.publish.assert_called_once()
    kwargs = publisher._client.publish.call_args.kwargs
    assert kwargs["TopicArn"] == "arn:aws:sns:test:topic"
    assert json.loads(kwargs["Message"])["event_type"] == "document_created"


@pytest.mark.asyncio
async def test_publish_swallows_sns_errors(monkeypatch):
    from app.services import event_publisher as ep_mod

    monkeypatch.setattr(ep_mod.settings, "sns_enabled", True)
    publisher = EventPublisher()
    publisher._client = MagicMock()
    publisher._client.publish.side_effect = RuntimeError("sns down")

    await publisher.publish("event", {})  # must not raise


def test_get_client_creates_boto3_client(monkeypatch):
    from app.services import event_publisher as ep_mod

    monkeypatch.setattr(ep_mod.settings, "aws_endpoint_url", "http://localhost:4566")
    publisher = EventPublisher()
    client = publisher._get_client()
    assert client is publisher._get_client()  # cached


# ---- Service-layer edge cases ----


@pytest.mark.asyncio
async def test_service_update_nonexistent(db_session):
    service = DocumentService(db_session)
    result = await service.update(
        uuid.uuid4(), DocumentUpdate(title="X", content="Y")
    )
    assert result is None


@pytest.mark.asyncio
async def test_service_patch_nonexistent(db_session):
    service = DocumentService(db_session)
    assert await service.patch(uuid.uuid4(), DocumentPatch(title="X")) is None


@pytest.mark.asyncio
async def test_service_patch_all_fields(db_session, owner_id, folder_id):
    from app.schemas.document import DocumentCreate

    service = DocumentService(db_session)
    doc = await service.create(
        DocumentCreate(title="T", content="C", owner_id=owner_id)
    )
    patched = await service.patch(
        doc.id,
        DocumentPatch(
            title="T2",
            content="new content here",
            content_type="text/plain",
            folder_id=folder_id,
        ),
    )
    assert patched is not None
    assert patched.title == "T2"
    assert patched.content == "new content here"
    assert patched.content_type == "text/plain"
    assert patched.folder_id == folder_id
    assert patched.word_count == 3
    assert patched.version == 2


@pytest.mark.asyncio
async def test_service_patch_no_fields_no_version_bump(db_session, owner_id):
    from app.schemas.document import DocumentCreate

    service = DocumentService(db_session)
    doc = await service.create(
        DocumentCreate(title="T", content="C", owner_id=owner_id)
    )
    patched = await service.patch(doc.id, DocumentPatch())
    assert patched is not None
    assert patched.version == 1


@pytest.mark.asyncio
async def test_service_restore_version_nonexistent_document(db_session):
    service = DocumentService(db_session)
    assert await service.restore_version(uuid.uuid4(), uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_service_restore_version_nonexistent_version(db_session, owner_id):
    from app.schemas.document import DocumentCreate

    service = DocumentService(db_session)
    doc = await service.create(
        DocumentCreate(title="T", content="C", owner_id=owner_id)
    )
    assert await service.restore_version(doc.id, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_service_delete_comment_nonexistent(db_session, owner_id):
    from app.schemas.document import DocumentCreate

    service = DocumentService(db_session)
    doc = await service.create(
        DocumentCreate(title="T", content="C", owner_id=owner_id)
    )
    assert await service.delete_comment(doc.id, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_service_update_with_updated_by(db_session, owner_id):
    from app.schemas.document import DocumentCreate

    service = DocumentService(db_session)
    doc = await service.create(
        DocumentCreate(title="T", content="C", owner_id=owner_id)
    )
    editor = uuid.uuid4()
    updated = await service.update(
        doc.id, DocumentUpdate(title="T2", content="C2"), updated_by=editor
    )
    assert updated is not None
    versions = await service.list_versions(doc.id)
    assert versions[0].created_by == editor


# ---- Schema validation ----


def test_document_patch_rejects_explicit_null():
    with pytest.raises(ValidationError):
        DocumentPatch(title=None)
    with pytest.raises(ValidationError):
        DocumentPatch(content=None)
    with pytest.raises(ValidationError):
        DocumentPatch(content_type=None)
