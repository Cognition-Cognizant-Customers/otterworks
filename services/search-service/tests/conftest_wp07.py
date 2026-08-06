"""Shared fixtures for the WP-07 test modules.

Kept out of ``conftest.py`` so the pre-existing fixtures are untouched; each
WP-07 module imports what it needs from here explicitly.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from app.config import AppConfig, AuthConfig, MeiliSearchConfig, SQSConfig
from app.main import create_app
from app.services.meilisearch_client import MeiliSearchService
from tests.fakes import FakeMeiliClient

DOCUMENTS_INDEX = "wp07-documents"
FILES_INDEX = "wp07-files"


def build_config(
    require_auth: bool = False,
    service_token: str = "",
    sqs_enabled: bool = False,
) -> AppConfig:
    """Build an isolated AppConfig; no environment variables are read."""
    return AppConfig(
        service_name="search-service-test",
        host="127.0.0.1",
        port=8087,
        debug=False,
        log_level="WARNING",
        meilisearch=MeiliSearchConfig(
            url="http://meilisearch.invalid:7700",
            api_key="",
            documents_index=DOCUMENTS_INDEX,
            files_index=FILES_INDEX,
        ),
        sqs=SQSConfig(
            queue_url="",
            region="us-east-1",
            endpoint_url="",
            enabled=sqs_enabled,
        ),
        auth=AuthConfig(service_token=service_token, require_auth=require_auth),
    )


@contextmanager
def fake_meili(client: FakeMeiliClient) -> Iterator[FakeMeiliClient]:
    """Patch ``meilisearch.Client`` so the service talks to the fake."""
    with patch("app.services.meilisearch_client.meilisearch.Client") as factory:
        factory.return_value = client
        yield client


def build_app(fake_client: FakeMeiliClient, config: AppConfig | None = None) -> Any:
    """Create a Flask app wired to the given fake MeiliSearch client."""
    with fake_meili(fake_client):
        app = create_app(config or build_config())
        app.config["TESTING"] = True
        return app


def build_service(fake_client: FakeMeiliClient) -> MeiliSearchService:
    """Create a ``MeiliSearchService`` wired to the given fake client."""
    with fake_meili(fake_client):
        return MeiliSearchService(build_config().meilisearch)


def seed_documents(fake_client: FakeMeiliClient, *documents: dict[str, Any]) -> None:
    index = fake_client.index(DOCUMENTS_INDEX)
    for document in documents:
        index.documents[str(document["id"])] = {"type": "document", **document}


def seed_files(fake_client: FakeMeiliClient, *files: dict[str, Any]) -> None:
    index = fake_client.index(FILES_INDEX)
    for file_data in files:
        index.documents[str(file_data["id"])] = {"type": "file", **file_data}


def last_search_params(fake_client: FakeMeiliClient, index_name: str) -> dict[str, Any]:
    """Return the params of the most recent search against an index."""
    calls = fake_client.index(index_name).search_calls
    assert calls, f"no search was issued against {index_name}"
    return calls[-1][1]
