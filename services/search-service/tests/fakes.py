"""In-memory fakes for MeiliSearch and SQS.

These fakes are deliberately small but *behavioural*: the MeiliSearch fake
enforces primary-key upsert semantics (so idempotency can be observed), and
applies the ``filter`` expression the service builds (so tenant scoping can be
observed end to end).  Nothing here talks to a network.
"""

from __future__ import annotations

import json
import re
from typing import Any

import meilisearch.errors
import requests

# ---------------------------------------------------------------------------
# MeiliSearch error factories
# ---------------------------------------------------------------------------


def make_api_error(
    status_code: int = 500,
    message: str = "internal server error",
    code: str = "internal",
) -> meilisearch.errors.MeilisearchApiError:
    """Build a real ``MeilisearchApiError`` with a given upstream status."""
    response = requests.models.Response()
    response.status_code = status_code
    response._content = json.dumps(
        {
            "message": message,
            "code": code,
            "type": "internal",
            "link": "https://docs.meilisearch.com/errors#internal",
        }
    ).encode()
    return meilisearch.errors.MeilisearchApiError(message, response)


def make_communication_error(
    message: str = "connection refused",
) -> meilisearch.errors.MeilisearchCommunicationError:
    return meilisearch.errors.MeilisearchCommunicationError(message)


def make_timeout_error(
    message: str = "timed out waiting for meilisearch",
) -> meilisearch.errors.MeilisearchTimeoutError:
    return meilisearch.errors.MeilisearchTimeoutError(message)


# ---------------------------------------------------------------------------
# Filter evaluation
# ---------------------------------------------------------------------------

_CLAUSE = re.compile(r'^(?P<field>\w+)\s*(?P<op>>=|<=|=)\s*"(?P<value>.*)"$', re.DOTALL)


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _clause_matches(doc: dict[str, Any], clause: str) -> bool:
    match = _CLAUSE.match(clause.strip())
    if not match:
        raise ValueError(f"fake meilisearch cannot parse filter clause: {clause!r}")
    field = match.group("field")
    op = match.group("op")
    value = _unescape(match.group("value"))
    actual = doc.get(field)
    if op == "=":
        if isinstance(actual, list):
            return value in actual
        return actual == value
    if actual is None:
        return False
    if op == ">=":
        return str(actual) >= value
    return str(actual) <= value


def _filter_matches(doc: dict[str, Any], expression: str | None) -> bool:
    if not expression:
        return True
    for group in expression.split(" AND "):
        group = group.strip()
        if group.startswith("(") and group.endswith(")"):
            alternatives = group[1:-1].split(" OR ")
            if not any(_clause_matches(doc, alt) for alt in alternatives):
                return False
        elif not _clause_matches(doc, group):
            return False
    return True


# ---------------------------------------------------------------------------
# MeiliSearch fake
# ---------------------------------------------------------------------------


class FakeTask:
    """Stand-in for a MeiliSearch task handle."""

    def __init__(self, task_uid: int) -> None:
        self.task_uid = task_uid


class FakeTaskResult:
    def __init__(self, status: str = "succeeded", error: Any = None) -> None:
        self.status = status
        self.error = error


class FakeIndex:
    """In-memory index keyed by the ``id`` primary key."""

    def __init__(self, name: str, client: FakeMeiliClient) -> None:
        self.name = name
        self._client = client
        self.documents: dict[str, dict[str, Any]] = {}
        self.search_calls: list[tuple[str, dict[str, Any]]] = []
        self.settings: dict[str, Any] = {}
        self.search_error: Exception | None = None

    # -- indexing ----------------------------------------------------------
    def add_documents(self, documents: list[dict[str, Any]]) -> FakeTask:
        for document in documents:
            self.documents[str(document["id"])] = dict(document)
        return self._client._next_task()

    def get_document(self, doc_id: str) -> dict[str, Any]:
        if str(doc_id) not in self.documents:
            raise make_api_error(404, "Document not found.", "document_not_found")
        return self.documents[str(doc_id)]

    def delete_document(self, doc_id: str) -> FakeTask:
        self.documents.pop(str(doc_id), None)
        return self._client._next_task()

    # -- settings ----------------------------------------------------------
    def update_searchable_attributes(self, attributes: list[str]) -> FakeTask:
        self.settings["searchable"] = attributes
        return self._client._next_task()

    def update_filterable_attributes(self, attributes: list[str]) -> FakeTask:
        self.settings["filterable"] = attributes
        return self._client._next_task()

    def update_sortable_attributes(self, attributes: list[str]) -> FakeTask:
        self.settings["sortable"] = attributes
        return self._client._next_task()

    def update_ranking_rules(self, rules: list[str]) -> FakeTask:
        self.settings["ranking"] = rules
        return self._client._next_task()

    # -- search ------------------------------------------------------------
    def search(self, query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self.search_calls.append((query, dict(params)))
        if self.search_error is not None:
            raise self.search_error

        matched = [
            doc
            for doc in self.documents.values()
            if _filter_matches(doc, params.get("filter"))
            and _text_matches(doc, query)
        ]
        matched.sort(key=lambda doc: str(doc.get("id", "")))
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 20))
        window = matched[offset : offset + limit] if limit > 0 else []
        return {
            "hits": [_with_formatted(doc, query) for doc in window],
            "estimatedTotalHits": len(matched),
            "offset": offset,
            "limit": limit,
        }


def _text_matches(doc: dict[str, Any], query: str) -> bool:
    """Naive prefix-ish match over the searchable text fields."""
    if not query.strip():
        return True
    haystack = " ".join(
        str(doc.get(field, "")) for field in ("title", "name", "content")
    ).lower()
    return all(term in haystack for term in query.lower().split())


def _with_formatted(doc: dict[str, Any], query: str) -> dict[str, Any]:
    hit = dict(doc)
    formatted: dict[str, Any] = {}
    for field in ("title", "name", "content"):
        value = doc.get(field)
        if value is None:
            continue
        text = str(value)
        term = query.strip().split()[0] if query.strip() else ""
        if term and term.lower() in text.lower():
            formatted[field] = text.replace(term, f"<em>{term}</em>")
        else:
            formatted[field] = text
    hit["_formatted"] = formatted
    return hit


class FakeMeiliClient:
    """Minimal stand-in for ``meilisearch.Client``."""

    def __init__(self, healthy: bool = True) -> None:
        self.indices: dict[str, FakeIndex] = {}
        self.healthy = healthy
        self.task_counter = 0
        self.deleted_indices: list[str] = []
        self.created_indices: list[str] = []
        self.task_status = "succeeded"
        self.wait_error: Exception | None = None

    def _next_task(self) -> FakeTask:
        self.task_counter += 1
        return FakeTask(self.task_counter)

    def index(self, name: str) -> FakeIndex:
        return self.indices.setdefault(name, FakeIndex(name, self))

    def get_index(self, name: str) -> FakeIndex:
        if name not in self.indices:
            raise make_api_error(404, "Index not found.", "index_not_found")
        return self.indices[name]

    def create_index(self, name: str, options: dict[str, Any] | None = None) -> FakeTask:
        self.created_indices.append(name)
        self.indices.setdefault(name, FakeIndex(name, self))
        return self._next_task()

    def delete_index(self, name: str) -> FakeTask:
        self.deleted_indices.append(name)
        self.indices.pop(name, None)
        return self._next_task()

    def wait_for_task(self, task_uid: int, timeout_in_ms: int = 5000) -> FakeTaskResult:
        if self.wait_error is not None:
            raise self.wait_error
        return FakeTaskResult(self.task_status)

    def health(self) -> dict[str, str]:
        if not self.healthy:
            raise make_communication_error()
        return {"status": "available"}


# ---------------------------------------------------------------------------
# SQS fake
# ---------------------------------------------------------------------------


class FakeSQSClient:
    """Records the SQS calls the consumer makes.

    ``deleted`` is the ack log: a message that is *not* in it was left on the
    queue and will reappear after the visibility timeout (and eventually be
    redriven to the DLQ by the queue's redrive policy).
    """

    def __init__(self, batches: list[list[dict[str, Any]]] | None = None) -> None:
        self.batches = list(batches or [])
        self.deleted: list[str] = []
        self.receive_calls: list[dict[str, Any]] = []
        self.changed_visibility: list[tuple[str, int]] = []

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        self.receive_calls.append(kwargs)
        if self.batches:
            return {"Messages": self.batches.pop(0)}
        return {}

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> dict[str, Any]:
        self.deleted.append(ReceiptHandle)
        return {}

    def change_message_visibility(
        self, QueueUrl: str, ReceiptHandle: str, VisibilityTimeout: int
    ) -> dict[str, Any]:
        self.changed_visibility.append((ReceiptHandle, VisibilityTimeout))
        return {}


def sqs_message(
    body: Any,
    receipt_handle: str = "receipt-1",
    message_id: str = "msg-1",
) -> dict[str, Any]:
    """Build an SQS message envelope; ``body`` may be a str or a JSON-able object."""
    raw = body if isinstance(body, str) else json.dumps(body)
    return {
        "MessageId": message_id,
        "ReceiptHandle": receipt_handle,
        "Body": raw,
    }
