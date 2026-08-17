#!/usr/bin/env python3
# search_reindex_weekly.py - Weekly full reindex of MeiliSearch
# Originally Python 2.7, minimally ported to Python 3 in 2021
# Clears MeiliSearch indices, paginates through document-service and
# file-service APIs, bulk-indexes into MeiliSearch, validates counts
#
# Owner: Jake (data-team@otterworks.dev) -- Jake left mid-2020
# TODO ETL-112: Add retry logic for transient API failures (2019-12-20)
# TODO ETL-145: Use connection pooling for requests (deferred Q3 2020)
# TODO ETL-188: Add timeout handling everywhere (never done)

import configparser
import json
import sys
import time
from datetime import datetime

import requests


DOCUMENTS_INDEX = "documents"
FILES_INDEX = "files"
API_PAGE_SIZE = 100
TASK_TIMEOUT_SECONDS = 60
INDEX_TASK_TIMEOUT_SECONDS = 120

DOCS_SETTINGS = {
    "searchableAttributes": ["title", "content", "tags"],
    "filterableAttributes": ["type", "owner_id", "tags", "created_at", "updated_at"],
    "sortableAttributes": ["updated_at", "created_at"],
    "rankingRules": ["words", "typo", "proximity", "attribute", "sort", "exactness"],
}
FILES_SETTINGS = {
    "searchableAttributes": ["name", "tags", "mime_type"],
    "filterableAttributes": ["type", "owner_id", "mime_type", "folder_id", "tags", "created_at", "updated_at"],
    "sortableAttributes": ["updated_at", "created_at", "size"],
    "rankingRules": ["words", "typo", "proximity", "attribute", "sort", "exactness"],
}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config():
    config = configparser.ConfigParser()
    config.read("/opt/etl/config.ini")
    return config


def meili_headers(api_key):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer %s" % api_key
    return headers


def wait_for_task(meili_url, headers, task_uid, timeout=TASK_TIMEOUT_SECONDS):
    """Poll a MeiliSearch task until it terminates. Returns the final task, or None."""
    if task_uid is None:
        return None

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task_resp = requests.get(
            "%s/tasks/%s" % (meili_url, task_uid),
            headers=headers,
        )
        task_resp.raise_for_status()
        task = task_resp.json()
        if task.get("status") in ("succeeded", "failed"):
            return task
        time.sleep(1)
    return None


def task_failed(task):
    return bool(task) and task.get("status") == "failed"


def delete_index(meili_url, headers, index_name):
    """Delete an index if it exists, waiting for the delete task to settle."""
    try:
        resp = requests.delete(
            "%s/indexes/%s" % (meili_url, index_name),
            headers=headers,
        )
        if resp.ok:
            task = wait_for_task(meili_url, headers, resp.json().get("taskUid"))
            if task_failed(task):
                print("[%s] WARNING: Delete task %s failed" % (now_str(), task.get("uid")))
            print("[%s] Deleted index: %s" % (now_str(), index_name))
    except:
        print("[%s] Index %s did not exist, skipping delete" % (now_str(), index_name))


def create_index(meili_url, headers, index_name):
    resp = requests.post(
        "%s/indexes" % meili_url,
        headers=headers,
        data=json.dumps({"uid": index_name, "primaryKey": "id"}),
    )
    resp.raise_for_status()
    wait_for_task(meili_url, headers, resp.json().get("taskUid"))
    print("[%s] Created index: %s" % (now_str(), index_name))


def configure_index(meili_url, headers, index_name, settings):
    resp = requests.patch(
        "%s/indexes/%s/settings" % (meili_url, index_name),
        headers=headers,
        data=json.dumps(settings),
    )
    resp.raise_for_status()
    wait_for_task(meili_url, headers, resp.json().get("taskUid"))


def index_batch(meili_url, headers, index_name, batch):
    """Push one batch of documents and wait for the indexing task."""
    index_resp = requests.post(
        "%s/indexes/%s/documents" % (meili_url, index_name),
        headers=headers,
        data=json.dumps(batch),
    )
    index_resp.raise_for_status()
    task = wait_for_task(
        meili_url, headers, index_resp.json().get("taskUid"), INDEX_TASK_TIMEOUT_SECONDS
    )
    if task_failed(task):
        print("[%s] WARNING: Index task failed: %s" % (now_str(), task.get("error", {})))


def to_document(doc):
    return {
        "id": doc.get("document_id", doc.get("id", "")),
        "title": doc.get("title", ""),
        "content": doc.get("content", ""),
        "owner_id": doc.get("owner_id", ""),
        "tags": doc.get("tags", []),
        "type": "document",
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def to_file(f):
    return {
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


def fetch_page(list_url, page, size_param):
    """Fetch one page of source records. No session reuse, no timeout, no retry."""
    resp = requests.get(list_url, params={"page": page, size_param: API_PAGE_SIZE})
    resp.raise_for_status()
    return resp.json()


def reindex(meili_url, headers, index_name, list_url, size_param, items_key, transform):
    """Paginate a source API into a MeiliSearch index. Returns the number indexed."""
    indexed = 0
    page = 1

    while True:
        data = fetch_page(list_url, page, size_param)
        items = data.get(items_key, data.get("items", []))

        if not items:
            break

        index_batch(meili_url, headers, index_name, [transform(item) for item in items])
        indexed += len(items)

        if len(items) < API_PAGE_SIZE:
            break
        page += 1

    print("[%s] Indexed %d %s into %s" % (now_str(), indexed, items_key, index_name))
    return indexed


def index_document_count(meili_url, headers, index_name):
    stats = requests.get(
        "%s/indexes/%s/stats" % (meili_url, index_name),
        headers=headers,
    )
    stats.raise_for_status()
    return stats.json().get("numberOfDocuments", 0)


def validate_counts(meili_url, headers, docs_indexed, files_indexed):
    """Compare indexed counts with MeiliSearch stats; exit non-zero on mismatch."""
    print("[%s] Validating index counts..." % now_str())

    docs_count = index_document_count(meili_url, headers, DOCUMENTS_INDEX)
    files_count = index_document_count(meili_url, headers, FILES_INDEX)

    print("[%s] Validation: documents=%d (expected %d), files=%d (expected %d)" % (
        now_str(), docs_count, docs_indexed, files_count, files_indexed,
    ))

    if docs_count != docs_indexed:
        print("[%s] ERROR: Documents index count mismatch: %d != %d" % (
            now_str(), docs_count, docs_indexed
        ))
        sys.exit(1)
    if files_count != files_indexed:
        print("[%s] ERROR: Files index count mismatch: %d != %d" % (
            now_str(), files_count, files_indexed
        ))
        sys.exit(1)


def rebuild_indices(meili_url, headers):
    """Drop, recreate, and configure both indices."""
    print("[%s] Clearing MeiliSearch indices..." % now_str())
    for index_name in [DOCUMENTS_INDEX, FILES_INDEX]:
        delete_index(meili_url, headers, index_name)

    for index_name in [DOCUMENTS_INDEX, FILES_INDEX]:
        create_index(meili_url, headers, index_name)

    configure_index(meili_url, headers, DOCUMENTS_INDEX, DOCS_SETTINGS)
    configure_index(meili_url, headers, FILES_INDEX, FILES_SETTINGS)

    print("[%s] MeiliSearch indices configured" % now_str())


def main():
    print("[%s] search_reindex_weekly.py starting..." % now_str())

    config = load_config()
    document_service_url = config.get("services", "document_service_url")
    file_service_url = config.get("services", "file_service_url")
    meilisearch_url = config.get("services", "meilisearch_url")
    headers = meili_headers(config.get("services", "meilisearch_api_key"))

    rebuild_indices(meilisearch_url, headers)

    print("[%s] Indexing documents from document-service..." % now_str())
    docs_indexed = reindex(
        meilisearch_url,
        headers,
        DOCUMENTS_INDEX,
        "%s/api/v1/documents" % document_service_url,
        "size",
        "documents",
        to_document,
    )

    print("[%s] Indexing files from file-service..." % now_str())
    files_indexed = reindex(
        meilisearch_url,
        headers,
        FILES_INDEX,
        "%s/api/v1/files" % file_service_url,
        "page_size",
        "files",
        to_file,
    )

    validate_counts(meilisearch_url, headers, docs_indexed, files_indexed)

    print("[%s] search_reindex_weekly.py completed successfully" % now_str())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[%s] FATAL: %s" % (now_str(), str(e)))
        sys.exit(1)
