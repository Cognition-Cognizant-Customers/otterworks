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


def log(message):
    print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message))


def wait_for_task(meilisearch_url, headers, task_uid, timeout=60):
    """Poll a MeiliSearch task until it is terminal, returning its final payload."""
    if task_uid is None:
        return None

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task_resp = requests.get(
            "%s/tasks/%s" % (meilisearch_url, task_uid),
            headers=headers,
        )
        task_resp.raise_for_status()
        task = task_resp.json()
        if task.get("status") in ("succeeded", "failed"):
            return task
        time.sleep(1)

    return None


def delete_index(meilisearch_url, headers, index_name):
    """Delete an index, tolerating one that does not exist yet."""
    try:
        resp = requests.delete(
            "%s/indexes/%s" % (meilisearch_url, index_name),
            headers=headers,
        )
        if not resp.ok:
            return
        task = wait_for_task(meilisearch_url, headers, resp.json().get("taskUid"))
        if task and task.get("status") == "failed":
            log("WARNING: Delete task for index %s failed" % index_name)
        log("Deleted index: %s" % index_name)
    except Exception:
        log("Index %s did not exist, skipping delete" % index_name)


def create_index(meilisearch_url, headers, index_name):
    """Create an index keyed on `id`."""
    resp = requests.post(
        "%s/indexes" % meilisearch_url,
        headers=headers,
        data=json.dumps({"uid": index_name, "primaryKey": "id"}),
    )
    resp.raise_for_status()
    wait_for_task(meilisearch_url, headers, resp.json().get("taskUid"))
    log("Created index: %s" % index_name)


def configure_index(meilisearch_url, headers, index_name, settings):
    """Apply index settings and wait for them to be committed."""
    resp = requests.patch(
        "%s/indexes/%s/settings" % (meilisearch_url, index_name),
        headers=headers,
        data=json.dumps(settings),
    )
    resp.raise_for_status()
    wait_for_task(meilisearch_url, headers, resp.json().get("taskUid"))


def index_batch(meilisearch_url, headers, index_name, batch):
    """Push one batch of records and wait for the indexing task."""
    index_resp = requests.post(
        "%s/indexes/%s/documents" % (meilisearch_url, index_name),
        headers=headers,
        data=json.dumps(batch),
    )
    index_resp.raise_for_status()
    task = wait_for_task(
        meilisearch_url, headers, index_resp.json().get("taskUid"), timeout=120
    )
    if task and task.get("status") == "failed":
        log("WARNING: Index task failed: %s" % task.get("error", {}))


def fetch_page(source_url, params, result_keys):
    """Fetch one page from a service API and return its items."""
    resp = requests.get(source_url, params=params)
    resp.raise_for_status()
    data = resp.json()
    for key in result_keys:
        if key in data:
            return data[key]
    return []


def reindex(meilisearch_url, headers, index_name, source, to_record):
    """Paginate a service API and bulk-index every record it returns."""
    indexed = 0
    page = 1
    page_size = source["page_size"]

    while True:
        items = fetch_page(
            source["url"],
            {"page": page, source["size_param"]: page_size},
            source["result_keys"],
        )
        if not items:
            return indexed

        index_batch(
            meilisearch_url, headers, index_name, [to_record(i) for i in items]
        )
        indexed += len(items)

        if len(items) < page_size:
            return indexed
        page += 1


def document_record(doc):
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


def file_record(f):
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


def index_document_count(meilisearch_url, headers, index_name):
    """Return the number of documents MeiliSearch reports for an index."""
    stats = requests.get(
        "%s/indexes/%s/stats" % (meilisearch_url, index_name),
        headers=headers,
    )
    stats.raise_for_status()
    return stats.json().get("numberOfDocuments", 0)


def validate_count(index_name, actual, expected):
    """Exit non-zero when an index holds a different count than we indexed."""
    if actual != expected:
        log("ERROR: %s index count mismatch: %d != %d" % (index_name, actual, expected))
        sys.exit(1)


def main():
    log("search_reindex_weekly.py starting...")

    # ---- Load config ----
    config = configparser.ConfigParser()
    config.read("/opt/etl/config.ini")

    document_service_url = config.get("services", "document_service_url")
    file_service_url = config.get("services", "file_service_url")
    meilisearch_url = config.get("services", "meilisearch_url")
    meilisearch_api_key = config.get("services", "meilisearch_api_key")

    documents_index = "documents"
    files_index = "files"
    api_page_size = 100

    meili_headers = {"Content-Type": "application/json"}
    if meilisearch_api_key:
        meili_headers["Authorization"] = "Bearer %s" % meilisearch_api_key

    # ---- Clear existing indices ----
    log("Clearing MeiliSearch indices...")

    for index_name in [documents_index, files_index]:
        delete_index(meilisearch_url, meili_headers, index_name)

    # ---- Create indices ----
    for index_name in [documents_index, files_index]:
        create_index(meilisearch_url, meili_headers, index_name)

    # ---- Configure index settings ----
    configure_index(meilisearch_url, meili_headers, documents_index, DOCS_SETTINGS)
    configure_index(meilisearch_url, meili_headers, files_index, FILES_SETTINGS)

    log("MeiliSearch indices configured")

    # ---- Fetch and index documents ----
    log("Indexing documents from document-service...")

    docs_indexed = reindex(
        meilisearch_url,
        meili_headers,
        documents_index,
        {
            "url": "%s/api/v1/documents" % document_service_url,
            "size_param": "size",
            "page_size": api_page_size,
            "result_keys": ("documents", "items"),
        },
        document_record,
    )

    log("Indexed %d documents into %s" % (docs_indexed, documents_index))

    # ---- Fetch and index files ----
    log("Indexing files from file-service...")

    files_indexed = reindex(
        meilisearch_url,
        meili_headers,
        files_index,
        {
            "url": "%s/api/v1/files" % file_service_url,
            "size_param": "page_size",
            "page_size": api_page_size,
            "result_keys": ("files", "items"),
        },
        file_record,
    )

    log("Indexed %d files into %s" % (files_indexed, files_index))

    # ---- Validate index counts ----
    log("Validating index counts...")

    docs_count = index_document_count(meilisearch_url, meili_headers, documents_index)
    files_count = index_document_count(meilisearch_url, meili_headers, files_index)

    log("Validation: documents=%d (expected %d), files=%d (expected %d)" % (
        docs_count, docs_indexed, files_count, files_indexed
    ))

    validate_count("Documents", docs_count, docs_indexed)
    validate_count("Files", files_count, files_indexed)

    log("search_reindex_weekly.py completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[%s] FATAL: %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(e)))
        sys.exit(1)
