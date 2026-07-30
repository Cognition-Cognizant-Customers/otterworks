import json

import pytest

from conftest import load_script

search_reindex_weekly = load_script("search_reindex_weekly")

MEILI = "http://meilisearch:7700"
DOC_SVC = "http://document-service:8000"
FILE_SVC = "http://file-service:8083"


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.ok = status < 400

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError("HTTP %d" % self.status)


class FakeMeiliBackend:
    """Simulates MeiliSearch task-based API plus the two source services."""

    def __init__(self, documents, files):
        self.documents = documents
        self.files = files
        self.indexed = {"documents": [], "files": []}
        self.deleted_indices = []
        self.settings = {}
        self.task_counter = 0
        self.calls = []

    def _task(self):
        self.task_counter += 1
        return FakeResponse({"taskUid": self.task_counter})

    def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url))
        if url.startswith("%s/tasks/" % MEILI):
            return FakeResponse({"status": "succeeded"})
        if url.startswith("%s/indexes/documents/stats" % MEILI):
            return FakeResponse({"numberOfDocuments": len(self.indexed["documents"])})
        if url.startswith("%s/indexes/files/stats" % MEILI):
            return FakeResponse({"numberOfDocuments": len(self.indexed["files"])})
        if url.startswith("%s/api/v1/documents" % DOC_SVC):
            page, size = params["page"], params["size"]
            chunk = self.documents[(page - 1) * size:page * size]
            return FakeResponse({"documents": chunk})
        if url.startswith("%s/api/v1/files" % FILE_SVC):
            page, size = params["page"], params["page_size"]
            chunk = self.files[(page - 1) * size:page * size]
            return FakeResponse({"files": chunk})
        raise AssertionError("unexpected GET %s" % url)

    def post(self, url, headers=None, data=None):
        self.calls.append(("POST", url))
        if url == "%s/indexes" % MEILI:
            return self._task()
        if url.endswith("/indexes/documents/documents"):
            self.indexed["documents"].extend(json.loads(data))
            return self._task()
        if url.endswith("/indexes/files/documents"):
            self.indexed["files"].extend(json.loads(data))
            return self._task()
        raise AssertionError("unexpected POST %s" % url)

    def patch(self, url, headers=None, data=None):
        self.calls.append(("PATCH", url))
        index = url.split("/indexes/")[1].split("/")[0]
        self.settings[index] = json.loads(data)
        return self._task()

    def delete(self, url, headers=None):
        self.calls.append(("DELETE", url))
        self.deleted_indices.append(url.rsplit("/", 1)[-1])
        return self._task()


def wire(monkeypatch, backend):
    monkeypatch.setattr(search_reindex_weekly.requests, "get", backend.get)
    monkeypatch.setattr(search_reindex_weekly.requests, "post", backend.post)
    monkeypatch.setattr(search_reindex_weekly.requests, "patch", backend.patch)
    monkeypatch.setattr(search_reindex_weekly.requests, "delete", backend.delete)
    monkeypatch.setattr(search_reindex_weekly.time, "sleep", lambda s: None)


def test_full_reindex_paginates_and_validates(monkeypatch, fake_config):
    fake_config(search_reindex_weekly)

    documents = [
        {"document_id": "d%d" % i, "title": "Doc %d" % i, "content": "body",
         "owner_id": "u1", "tags": ["t"], "created_at": "2026-01-01", "updated_at": "2026-01-02"}
        for i in range(150)  # crosses the 100-item api_page_size boundary
    ]
    files = [
        {"file_id": "f%d" % i, "file_name": "file%d.txt" % i, "owner_id": "u2",
         "mime_type": "text/plain", "folder_id": "root", "size_bytes": 10 + i}
        for i in range(42)
    ]
    backend = FakeMeiliBackend(documents, files)
    wire(monkeypatch, backend)

    search_reindex_weekly.main()

    assert backend.deleted_indices == ["documents", "files"]
    assert len(backend.indexed["documents"]) == 150
    assert len(backend.indexed["files"]) == 42

    # field mapping into MeiliSearch schema
    doc0 = backend.indexed["documents"][0]
    assert doc0["id"] == "d0" and doc0["type"] == "document"
    file0 = backend.indexed["files"][0]
    assert file0["id"] == "f0" and file0["name"] == "file0.txt" and file0["size"] == 10

    # index settings configured for both indices
    assert backend.settings["documents"]["searchableAttributes"] == ["title", "content", "tags"]
    assert "mime_type" in backend.settings["files"]["searchableAttributes"]


def test_count_mismatch_exits_nonzero(monkeypatch, fake_config):
    fake_config(search_reindex_weekly)

    backend = FakeMeiliBackend(
        [{"document_id": "d1", "title": "x", "content": "y"}], []
    )

    original_get = backend.get

    def lying_get(url, headers=None, params=None):
        if url.startswith("%s/indexes/documents/stats" % MEILI):
            return FakeResponse({"numberOfDocuments": 999})
        return original_get(url, headers=headers, params=params)

    backend.get = lying_get
    wire(monkeypatch, backend)

    with pytest.raises(SystemExit) as exc:
        search_reindex_weekly.main()
    assert exc.value.code == 1


def test_auth_header_sent_to_meilisearch(monkeypatch, fake_config):
    fake_config(search_reindex_weekly)
    seen_headers = []

    backend = FakeMeiliBackend([], [])
    original_post = backend.post

    def spy_post(url, headers=None, data=None):
        seen_headers.append(headers)
        return original_post(url, headers=headers, data=data)

    backend.post = spy_post
    wire(monkeypatch, backend)

    search_reindex_weekly.main()

    assert all(h.get("Authorization") == "Bearer test-meili-key" for h in seen_headers)
    assert seen_headers  # index creation posts happened
