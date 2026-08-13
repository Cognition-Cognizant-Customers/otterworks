"""Lambda tasks for the otterworks-search-reindex state machine.

State machine flow:
  clear_indices -> fetch_and_index_documents + fetch_and_index_files (parallel)
    -> validate_indices

Runs inside the VPC so it can reach MeiliSearch and the internal service APIs.
"""

import json
import time
from typing import Any

import requests

from otterworks_etl.common.config import env, meilisearch_api_key
from otterworks_etl.common.dispatch import make_handler
from otterworks_etl.common.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "search-reindex"
DOCUMENTS_INDEX = "documents"
FILES_INDEX = "files"
API_PAGE_SIZE = 100
REQUEST_TIMEOUT = 30

DOCS_SETTINGS = {
    "searchableAttributes": ["title", "content", "tags"],
    "filterableAttributes": ["type", "owner_id", "tags", "created_at", "updated_at"],
    "sortableAttributes": ["updated_at", "created_at"],
    "rankingRules": ["words", "typo", "proximity", "attribute", "sort", "exactness"],
}
FILES_SETTINGS = {
    "searchableAttributes": ["name", "tags", "mime_type"],
    "filterableAttributes": [
        "type", "owner_id", "mime_type", "folder_id", "tags", "created_at", "updated_at",
    ],
    "sortableAttributes": ["updated_at", "created_at", "size"],
    "rankingRules": ["words", "typo", "proximity", "attribute", "sort", "exactness"],
}


def _session() -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504]
        )
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _meili_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    api_key = meilisearch_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _wait_for_task(
    session: requests.Session,
    task_uid: int,
    timeout: int = 120,
    raise_on_failure: bool = True,
) -> str:
    meili_url = env("MEILISEARCH_URL")
    deadline = time.monotonic() + timeout
    status = "timed_out"
    error = None
    while time.monotonic() < deadline:
        resp = session.get(
            f"{meili_url}/tasks/{task_uid}",
            headers=_meili_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        status = resp.json().get("status")
        if status == "succeeded":
            return status
        if status == "failed":
            error = resp.json().get("error")
            break
        time.sleep(1)

    logger.warning("meilisearch task did not succeed", extra={"context": {
        "task_uid": task_uid, "status": status, "error": error}})
    if raise_on_failure:
        raise RuntimeError(
            f"MeiliSearch task {task_uid} {status}: {error}"
        )
    return status


def clear_indices(event: dict) -> dict:
    meili_url = env("MEILISEARCH_URL")
    session = _session()
    headers = _meili_headers()

    for index_name in (DOCUMENTS_INDEX, FILES_INDEX):
        resp = session.delete(
            f"{meili_url}/indexes/{index_name}", headers=headers, timeout=REQUEST_TIMEOUT
        )
        if resp.ok:
            task_uid = resp.json().get("taskUid")
            if task_uid is not None:
                # a failed delete (e.g. index_not_found) is fine: the index is
                # recreated from scratch immediately below
                _wait_for_task(session, task_uid, timeout=60, raise_on_failure=False)

    for index_name in (DOCUMENTS_INDEX, FILES_INDEX):
        resp = session.post(
            f"{meili_url}/indexes",
            headers=headers,
            data=json.dumps({"uid": index_name, "primaryKey": "id"}),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        task_uid = resp.json().get("taskUid")
        if task_uid is not None:
            _wait_for_task(session, task_uid, timeout=60)

    for index_name, settings in (
        (DOCUMENTS_INDEX, DOCS_SETTINGS),
        (FILES_INDEX, FILES_SETTINGS),
    ):
        resp = session.patch(
            f"{meili_url}/indexes/{index_name}/settings",
            headers=headers,
            data=json.dumps(settings),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        task_uid = resp.json().get("taskUid")
        if task_uid is not None:
            _wait_for_task(session, task_uid, timeout=60)

    return {"indices_reset": [DOCUMENTS_INDEX, FILES_INDEX]}


def _index_batch(session: requests.Session, index_name: str, batch: list[dict]) -> None:
    resp = session.post(
        f"{env('MEILISEARCH_URL')}/indexes/{index_name}/documents",
        headers=_meili_headers(),
        data=json.dumps(batch),
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    task_uid = resp.json().get("taskUid")
    if task_uid is not None:
        _wait_for_task(session, task_uid)


def _paginate(
    session: requests.Session, url: str, page_param: str, list_keys: tuple[str, str]
):
    page = 1
    while True:
        resp = session.get(
            url, params={"page": page, page_param: API_PAGE_SIZE}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get(list_keys[0], data.get(list_keys[1], []))
        if not items:
            return
        yield items
        if len(items) < API_PAGE_SIZE:
            return
        page += 1


def fetch_and_index_documents(event: dict) -> dict:
    session = _session()
    url = f"{env('DOCUMENT_SERVICE_URL')}/api/v1/documents"
    indexed = 0

    for documents in _paginate(session, url, "size", ("documents", "items")):
        batch: list[dict[str, Any]] = [
            {
                "id": doc.get("document_id", doc.get("id", "")),
                "title": doc.get("title", ""),
                "content": doc.get("content", ""),
                "owner_id": doc.get("owner_id", ""),
                "tags": doc.get("tags", []),
                "type": "document",
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
            }
            for doc in documents
        ]
        _index_batch(session, DOCUMENTS_INDEX, batch)
        indexed += len(batch)

    return {"index": DOCUMENTS_INDEX, "indexed_count": indexed}


def fetch_and_index_files(event: dict) -> dict:
    session = _session()
    url = f"{env('FILE_SERVICE_URL')}/api/v1/files"
    indexed = 0

    for files in _paginate(session, url, "page_size", ("files", "items")):
        batch: list[dict[str, Any]] = [
            {
                "id": f.get("file_id", f.get("id", "")),
                "name": f.get("file_name", f.get("name", "")),
                "owner_id": f.get("owner_id", ""),
                "mime_type": f.get("mime_type", ""),
                "folder_id": f.get("folder_id", ""),
                "size": f.get("size_bytes", f.get("size", 0)),
                "tags": f.get("tags", []),
                "type": "file",
                "created_at": f.get("created_at"),
                "updated_at": f.get("updated_at"),
            }
            for f in files
        ]
        _index_batch(session, FILES_INDEX, batch)
        indexed += len(batch)

    return {"index": FILES_INDEX, "indexed_count": indexed}


def validate_indices(event: dict) -> dict:
    meili_url = env("MEILISEARCH_URL")
    session = _session()
    headers = _meili_headers()

    expected = {r["index"]: r["indexed_count"] for r in event["index_results"]}
    actual = {}
    for index_name in (DOCUMENTS_INDEX, FILES_INDEX):
        resp = session.get(
            f"{meili_url}/indexes/{index_name}/stats",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        actual[index_name] = resp.json().get("numberOfDocuments", 0)

    mismatches = {
        name: {"expected": expected.get(name, 0), "actual": count}
        for name, count in actual.items()
        if count != expected.get(name, 0)
    }
    if mismatches:
        raise RuntimeError(f"Index count mismatch: {mismatches}")

    return {"validated": actual}


handler = make_handler(PIPELINE, {
    "clear_indices": clear_indices,
    "fetch_and_index_documents": fetch_and_index_documents,
    "fetch_and_index_files": fetch_and_index_files,
    "validate_indices": validate_indices,
})
