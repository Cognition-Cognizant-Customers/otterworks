"""Pytest fixtures for Search Service tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, AuthConfig, MeiliSearchConfig, SQSConfig
from app.main import create_app
from app.services.meilisearch_client import MeiliSearchService


@pytest.fixture()
def app_config() -> AppConfig:
    """Create a test AppConfig with auth disabled."""
    return AppConfig(
        service_name="search-service-test",
        port=8087,
        debug=True,
        log_level="DEBUG",
        meilisearch=MeiliSearchConfig(
            url="http://localhost:7700",
            api_key="",
            documents_index="test-otterworks-documents",
            files_index="test-otterworks-files",
        ),
        sqs=SQSConfig(enabled=False),
        auth=AuthConfig(service_token="", require_auth=False),
    )


@pytest.fixture()
def mock_meilisearch_client() -> MagicMock:
    """Create a mock meilisearch.Client."""
    mock = MagicMock()

    mock_health = {"status": "available"}
    mock.health.return_value = mock_health

    mock_task = MagicMock()
    mock_task.task_uid = 1
    mock_task_result = MagicMock()
    mock_task_result.status = "succeeded"
    mock.wait_for_task.return_value = mock_task_result

    mock_index = MagicMock()
    mock_index.add_documents.return_value = mock_task
    mock_index.delete_document.return_value = mock_task
    mock_index.search.return_value = {"hits": [], "estimatedTotalHits": 0}
    mock.index.return_value = mock_index

    mock.create_index.return_value = mock_task
    mock.delete_index.return_value = mock_task
    mock.get_index.side_effect = None

    return mock


@pytest.fixture()
def app(app_config: AppConfig, mock_meilisearch_client: MagicMock):
    """Create a FastAPI test app with mocked MeiliSearch."""
    with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
        mock_cls.return_value = mock_meilisearch_client
        yield create_app(app_config)


class _Response:
    """Adapter exposing a Flask-test-client-like response interface."""

    def __init__(self, response: Any) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def data(self) -> bytes:
        return self._response.content

    def get_json(self) -> Any:
        return self._response.json()


class _Client:
    """Adapter exposing a Flask-test-client-like request interface."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def _request(self, method: str, path: str, **kwargs: Any) -> _Response:
        content_type = kwargs.pop("content_type", None)
        if content_type is not None:
            headers = dict(kwargs.pop("headers", None) or {})
            headers["Content-Type"] = content_type
            kwargs["headers"] = headers
        return _Response(self._client.request(method, path, **kwargs))

    def get(self, path: str, **kwargs: Any) -> _Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> _Response:
        return self._request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> _Response:
        return self._request("DELETE", path, **kwargs)


@pytest.fixture()
def client(app):
    """Create a test client for the FastAPI app."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield _Client(test_client)


@pytest.fixture()
def meilisearch_service(app_config: AppConfig, mock_meilisearch_client: MagicMock) -> MeiliSearchService:
    """Create a MeiliSearchService with a mocked client."""
    with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
        mock_cls.return_value = mock_meilisearch_client
        service = MeiliSearchService(app_config.meilisearch)
        return service
