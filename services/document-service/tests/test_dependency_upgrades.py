"""Regression tests for paths exercised by the FastAPI/Starlette/OTel/protobuf upgrades."""

import os
import uuid

import jwt
import pytest
from httpx import AsyncClient

TEST_JWT_SECRET = "test-jwt-secret-for-unit-tests-pad32"  # noqa: S105
os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = jwt.encode({"user_id": str(user_id)}, TEST_JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


async def _create_document(client: AsyncClient, owner_id: uuid.UUID, **overrides) -> dict:
    payload = {"title": "Doc", "content": "Body", "owner_id": str(owner_id)}
    payload.update(overrides)
    resp = await client.post("/api/v1/documents/", json=payload)
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_authenticated_document_lifecycle(client: AsyncClient, owner_id: uuid.UUID):
    """Full CRUD round-trip through the upgraded Starlette request pipeline."""
    headers = _auth_headers(owner_id)
    doc = await _create_document(client, owner_id)

    resp = await client.get(f"/api/v1/documents/{doc['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == doc["id"]

    resp = await client.put(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "Updated", "content": "New body"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated"
    assert resp.json()["version"] == 2

    resp = await client.patch(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "Patched"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Patched"

    resp = await client.delete(f"/api/v1/documents/{doc['id']}", headers=headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_authenticated_versions_and_restore(client: AsyncClient, owner_id: uuid.UUID):
    headers = _auth_headers(owner_id)
    doc = await _create_document(client, owner_id, content="v1")

    resp = await client.put(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "Doc", "content": "v2"},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.get(f"/api/v1/documents/{doc['id']}/versions", headers=headers)
    assert resp.status_code == 200
    versions = resp.json()
    assert sorted(v["version_number"] for v in versions) == [1, 2]

    v1_id = next(v["id"] for v in versions if v["version_number"] == 1)
    resp = await client.post(
        f"/api/v1/documents/{doc['id']}/versions/{v1_id}/restore", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "v1"


@pytest.mark.asyncio
async def test_authenticated_export(client: AsyncClient, owner_id: uuid.UUID):
    headers = _auth_headers(owner_id)
    doc = await _create_document(client, owner_id, title="Export Me", content="# Heading")

    resp = await client.get(
        f"/api/v1/documents/{doc['id']}/export",
        params={"format": "markdown"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert "Export Me" in resp.text

    resp = await client.get(
        f"/api/v1/documents/{doc['id']}/export",
        params={"format": "html"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_cross_owner_access_denied(client: AsyncClient, owner_id: uuid.UUID):
    doc = await _create_document(client, owner_id)
    other_headers = _auth_headers(uuid.uuid4())
    resp = await client.get(f"/api/v1/documents/{doc['id']}", headers=other_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invalid_bearer_token_rejected(client: AsyncClient, owner_id: uuid.UUID):
    doc = await _create_document(client, owner_id)
    resp = await client.get(
        f"/api/v1/documents/{doc['id']}",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_query_validation_errors(client: AsyncClient):
    """FastAPI 0.115 validation still returns 422 for invalid query params."""
    resp = await client.get("/api/v1/documents/", params={"page": 0})
    assert resp.status_code == 422

    resp = await client.get("/api/v1/documents/", params={"size": 1000})
    assert resp.status_code == 422

    resp = await client.get("/api/v1/documents/search", params={"q": ""})
    assert resp.status_code == 422


def test_openapi_schema_generation():
    """FastAPI 0.115 OpenAPI generation succeeds for all routes."""
    from app.main import app

    schema = app.openapi()
    assert schema["info"]["title"] == "OtterWorks Document Service"
    assert "/api/v1/documents/" in schema["paths"]
    assert "/api/v1/documents/{document_id}" in schema["paths"]


def test_settings_load():
    """pydantic-settings 2.14 still parses the service configuration."""
    from app.config import Settings

    settings = Settings()
    assert settings.app_version


def test_fastapi_instrumentor_importable_and_instruments_app():
    """OTel FastAPI instrumentation (0.51b0) works against the app object."""
    from fastapi import FastAPI
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    test_app = FastAPI()
    FastAPIInstrumentor.instrument_app(test_app)
    assert getattr(test_app, "_is_instrumented_by_opentelemetry", False)
    FastAPIInstrumentor.uninstrument_app(test_app)


def test_otlp_protobuf_span_encoding():
    """protobuf 5.x encodes OTel spans end-to-end via the OTLP proto encoder."""
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("upgrade-test")
    with tracer.start_as_current_span("test-span"):
        pass

    spans = exporter.get_finished_spans()
    request = encode_spans(spans)
    payload = request.SerializeToString()
    assert payload
    assert type(request)().FromString(payload).resource_spans
