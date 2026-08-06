"""In-memory MeiliSearch test doubles.

The fakes model the parts of the MeiliSearch client the search-service uses,
with the behaviour that matters for idempotency assertions: documents are
stored in a dict keyed by the ``id`` primary key, so re-adding the same
document overwrites rather than duplicates it.

Everything here is deterministic: no clocks, no threads, no network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import meilisearch


def api_error(message: str = "boom", status_code: int = 400) -> meilisearch.errors.MeilisearchApiError:
    """Build a MeilisearchApiError without touching the network."""
    return meilisearch.errors.MeilisearchApiError(
        message, SimpleNamespace(status_code=status_code, text="")
    )


class FakeTask:
    """Stand-in for the task handle returned by write operations."""

    def __init__(self, task_uid: int) -> None:
        self.task_uid = task_uid


class FakeIndex:
    """In-memory index keyed by the ``id`` primary key."""

    def __init__(self, name: str, client: FakeClient) -> None:
        self.name = name
        self._client = client
        self.documents: dict[str, dict[str, Any]] = {}
        self.add_documents_calls: list[list[dict[str, Any]]] = []
        self.search_calls: list[tuple[str, dict[str, Any]]] = []
        self.settings_calls: list[str] = []

    def add_documents(self, documents: list[dict[str, Any]]) -> FakeTask:
        self.add_documents_calls.append(documents)
        for doc in documents:
            self.documents[str(doc["id"])] = dict(doc)
        return self._client._next_task()

    def delete_document(self, doc_id: str) -> FakeTask:
        self.documents.pop(str(doc_id), None)
        return self._client._next_task()

    def get_document(self, doc_id: str) -> dict[str, Any]:
        try:
            return self.documents[str(doc_id)]
        except KeyError:
            raise api_error(f"Document `{doc_id}` not found.", 404) from None

    def search(self, query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self.search_calls.append((query, dict(params)))
        matches = [
            doc
            for doc in self.documents.values()
            if not query
            or query.lower()
            in " ".join(
                str(doc.get(field, "")) for field in ("title", "name", "content")
            ).lower()
        ]
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 20))
        return {
            "hits": matches[offset : offset + limit],
            "estimatedTotalHits": len(matches),
        }

    def update_searchable_attributes(self, attrs: list[str]) -> FakeTask:
        self.settings_calls.append("searchable")
        return self._client._next_task()

    def update_filterable_attributes(self, attrs: list[str]) -> FakeTask:
        self.settings_calls.append("filterable")
        return self._client._next_task()

    def update_sortable_attributes(self, attrs: list[str]) -> FakeTask:
        self.settings_calls.append("sortable")
        return self._client._next_task()

    def update_ranking_rules(self, rules: list[str]) -> FakeTask:
        self.settings_calls.append("ranking")
        return self._client._next_task()


class FakeClient:
    """In-memory stand-in for ``meilisearch.Client``."""

    def __init__(self) -> None:
        self.indices: dict[str, FakeIndex] = {}
        self.created_indices: list[str] = []
        self.deleted_indices: list[str] = []
        self._task_uid = 0
        self.healthy = True

    def _next_task(self) -> FakeTask:
        self._task_uid += 1
        return FakeTask(self._task_uid)

    def health(self) -> dict[str, str]:
        if not self.healthy:
            raise meilisearch.errors.MeilisearchCommunicationError("unreachable")
        return {"status": "available"}

    def index(self, name: str) -> FakeIndex:
        return self.indices.setdefault(name, FakeIndex(name, self))

    def get_index(self, name: str) -> FakeIndex:
        if name not in self.indices:
            raise api_error(f"Index `{name}` not found.", 404)
        return self.indices[name]

    def create_index(self, name: str, options: dict[str, Any] | None = None) -> FakeTask:
        self.created_indices.append(name)
        self.indices.setdefault(name, FakeIndex(name, self))
        return self._next_task()

    def delete_index(self, name: str) -> FakeTask:
        self.deleted_indices.append(name)
        self.indices.pop(name, None)
        return self._next_task()

    def wait_for_task(self, task_uid: int, timeout_in_ms: int = 5000) -> SimpleNamespace:
        return SimpleNamespace(status="succeeded", error=None)
