"""Amazon OpenSearch client for full-text search operations.

Sibling adapter to :mod:`app.services.meilisearch_client` exposing the same
public surface (``ensure_indices``, ``ping``, ``search``, ``advanced_search``,
``suggest``, ``index_document``, ``index_file``, ``delete_document``,
``reindex``) so the API layer can select it via ``SEARCH_BACKEND=opensearch``.

Works against both a self-managed / local OpenSearch node and Amazon
OpenSearch Serverless (SigV4 auth via ``OPENSEARCH_AWS_AUTH=true``,
``OPENSEARCH_SERVICE=aoss``).
"""

from __future__ import annotations

from typing import Any

import structlog
from opensearchpy import NotFoundError, OpenSearch, RequestError, RequestsHttpConnection

from app.config import OpenSearchConfig
from app.models.search_result import SearchHit, SearchResponse
from app.services.meilisearch_client import record_search_analytics

logger = structlog.get_logger()

# Date mapping accepts full ISO timestamps and date-only values; a date-only
# upper bound (e.g. lte 2024-06-30) is rounded up to the end of that day.
DATE_FIELD: dict[str, Any] = {"type": "date", "format": "strict_date_optional_time||epoch_millis"}

DOCUMENTS_MAPPINGS: dict[str, Any] = {
    "properties": {
        "id": {"type": "keyword"},
        "title": {"type": "text"},
        "content": {"type": "text"},
        "tags": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
        "type": {"type": "keyword"},
        "owner_id": {"type": "keyword"},
        "created_at": DATE_FIELD,
        "updated_at": DATE_FIELD,
    }
}

FILES_MAPPINGS: dict[str, Any] = {
    "properties": {
        "id": {"type": "keyword"},
        "name": {"type": "text"},
        "tags": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
        "mime_type": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
        "type": {"type": "keyword"},
        "owner_id": {"type": "keyword"},
        "folder_id": {"type": "keyword"},
        "size": {"type": "long"},
        "created_at": DATE_FIELD,
        "updated_at": DATE_FIELD,
    }
}

# OpenSearch rejects from + size beyond index.max_result_window (default 10000);
# requests past it return an empty page (with the real total) like MeiliSearch.
MAX_RESULT_WINDOW = 10000

DOCUMENTS_SEARCH_FIELDS = ["title^3", "content", "tags"]
FILES_SEARCH_FIELDS = ["name^3", "tags", "mime_type"]

HIGHLIGHT_CONFIG: dict[str, Any] = {
    "pre_tags": ["<em>"],
    "post_tags": ["</em>"],
    "fields": {
        "title": {},
        "name": {},
        "content": {"fragment_size": 200, "number_of_fragments": 1},
    },
}


class OpenSearchService:
    """Client for Amazon OpenSearch search and indexing operations."""

    def __init__(self, config: OpenSearchConfig) -> None:
        self.config = config
        self.documents_index_name = config.documents_index
        self.files_index_name = config.files_index
        self.client = self._build_client(config)
        self._indices_ready = False

    @staticmethod
    def _build_client(config: OpenSearchConfig) -> OpenSearch:
        kwargs: dict[str, Any] = {
            "hosts": [config.url],
            "timeout": 30,
            "max_retries": 2,
            "retry_on_timeout": True,
        }
        if config.aws_auth:
            import boto3
            from opensearchpy import AWSV4SignerAuth

            credentials = boto3.Session().get_credentials()
            kwargs.update(
                http_auth=AWSV4SignerAuth(credentials, config.region, config.service),
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                pool_maxsize=20,
            )
        return OpenSearch(**kwargs)

    def _refresh_kwargs(self) -> dict[str, Any]:
        """Return refresh parameters where supported (not on Serverless)."""
        return {"refresh": "true"} if self.config.supports_refresh else {}

    def ensure_indices(self) -> None:
        """Create indices with mappings if they don't exist."""
        for index_name, mappings in [
            (self.documents_index_name, DOCUMENTS_MAPPINGS),
            (self.files_index_name, FILES_MAPPINGS),
        ]:
            if not self.client.indices.exists(index=index_name):
                self.client.indices.create(index=index_name, body={"mappings": mappings})
                logger.info("opensearch_index_created", index=index_name)
        self._indices_ready = True
        logger.info("opensearch_indices_configured")

    def _ensure_ready(self) -> None:
        """Lazily retry index bootstrap if it hasn't succeeded yet.

        Writing to a missing index would auto-create it with dynamic mappings
        (breaking the tags.raw / owner_id filters), so indexing must never
        proceed until the explicit mappings are in place.
        """
        if not self._indices_ready:
            self.ensure_indices()

    def ping(self) -> bool:
        """Check if OpenSearch is reachable."""
        try:
            if self.client.ping():
                return True
            # OpenSearch Serverless does not expose the root endpoint; probe
            # an index-level API instead.
            self.client.indices.exists(index=self.documents_index_name)
            return True
        except Exception:
            return False

    @staticmethod
    def _build_filters(
        doc_type: str | None = None,
        owner_id: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []
        if doc_type:
            filters.append({"term": {"type": doc_type}})
        if owner_id:
            filters.append({"term": {"owner_id": owner_id}})
        if tags:
            filters.append({"terms": {"tags.raw": tags}})
        date_range: dict[str, Any] = {}
        if date_from:
            date_range["gte"] = date_from
        if date_to:
            date_range["lte"] = date_to
        if date_range:
            filters.append({"range": {"created_at": date_range}})
        return filters

    def _query_index(
        self,
        index_name: str,
        query: str,
        filters: list[dict[str, Any]],
        size: int,
        from_: int,
    ) -> tuple[list[SearchHit], int]:
        """Run a search against one index, returning hits and the total count."""
        fields = (
            DOCUMENTS_SEARCH_FIELDS
            if index_name == self.documents_index_name
            else FILES_SEARCH_FIELDS
        )
        if query:
            match_query: dict[str, Any] = {
                "multi_match": {
                    "query": query,
                    "fields": fields,
                    "fuzziness": "AUTO",
                    "operator": "or",
                }
            }
        else:
            match_query = {"match_all": {}}

        beyond_window = from_ + size > MAX_RESULT_WINDOW
        body: dict[str, Any] = {
            "query": {"bool": {"must": [match_query], "filter": filters}},
            "from": 0 if beyond_window else from_,
            "size": 0 if beyond_window else size,
            "highlight": HIGHLIGHT_CONFIG,
            "track_total_hits": True,
        }
        try:
            result = self.client.search(index=index_name, body=body)
        except RequestError as exc:
            logger.warning("search_filter_error", index=index_name, error=str(exc))
            raise ValueError(f"Invalid search filter: {exc}") from exc

        total = result["hits"]["total"]["value"]
        hits = [self._parse_hit(hit, index_name) for hit in result["hits"]["hits"]]
        return hits, total

    def _run_search(
        self,
        query: str,
        filters: list[dict[str, Any]],
        doc_type: str | None,
        page: int,
        page_size: int,
    ) -> SearchResponse:
        self._ensure_ready()
        indices_to_search = self._resolve_indices(doc_type)
        multi_index = len(indices_to_search) > 1

        if multi_index:
            fetch_size = page * page_size
            from_ = 0
        else:
            fetch_size = page_size
            from_ = (page - 1) * page_size

        all_hits: list[SearchHit] = []
        total = 0
        for index_name in indices_to_search:
            hits, index_total = self._query_index(index_name, query, filters, fetch_size, from_)
            total += index_total
            all_hits.extend(hits)

        record_search_analytics(query or "*", total)

        start = (page - 1) * page_size if multi_index else 0
        page_hits = all_hits[start : start + page_size]

        return SearchResponse(
            results=page_hits,
            total=total,
            page=page,
            page_size=page_size,
            query=query or "*",
        )

    def search(
        self,
        query: str,
        doc_type: str | None = None,
        owner_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SearchResponse:
        """Full-text search across documents and files."""
        filters = self._build_filters(doc_type=doc_type, owner_id=owner_id)
        response = self._run_search(query, filters, doc_type, page, page_size)
        return SearchResponse(
            results=response.results,
            total=response.total,
            page=response.page,
            page_size=response.page_size,
            query=query,
        )

    def advanced_search(
        self,
        query: str | None = None,
        doc_type: str | None = None,
        owner_id: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SearchResponse:
        """Advanced search with detailed filters."""
        filters = self._build_filters(
            doc_type=doc_type,
            owner_id=owner_id,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
        )
        return self._run_search(query or "", filters, doc_type, page, page_size)

    def suggest(self, prefix: str, size: int = 10) -> list[str]:
        """Autocomplete suggestions using prefix matching.

        MeiliSearch is prefix-first out of the box; a plain OpenSearch
        ``match`` query is not, so the suggest path uses
        ``match_phrase_prefix`` to preserve type-ahead semantics.
        """
        self._ensure_ready()
        suggestions: list[str] = []
        seen: set[str] = set()

        for index_name, field in [
            (self.documents_index_name, "title"),
            (self.files_index_name, "name"),
        ]:
            body = {
                "query": {"match_phrase_prefix": {field: {"query": prefix}}},
                "size": size,
                "_source": [field],
            }
            result = self.client.search(index=index_name, body=body)
            for hit in result["hits"]["hits"]:
                text = hit["_source"].get(field, "")
                if text and text not in seen:
                    suggestions.append(text)
                    seen.add(text)
                    if len(suggestions) >= size:
                        break
            if len(suggestions) >= size:
                break

        return suggestions

    def index_document(self, document: dict[str, Any]) -> None:
        """Index or update a document."""
        self._ensure_ready()
        doc = {**document, "type": "document"}
        self.client.index(
            index=self.documents_index_name,
            id=doc["id"],
            body=doc,
            **self._refresh_kwargs(),
        )
        logger.info("document_indexed", document_id=doc.get("id"))

    def index_file(self, file_data: dict[str, Any]) -> None:
        """Index or update a file."""
        self._ensure_ready()
        doc = {**file_data, "type": "file"}
        self.client.index(
            index=self.files_index_name,
            id=doc["id"],
            body=doc,
            **self._refresh_kwargs(),
        )
        logger.info("file_indexed", file_id=doc.get("id"))

    def delete_document(self, doc_type: str, doc_id: str) -> bool:
        """Remove a document or file from the index. Returns False if not found."""
        index_name = (
            self.documents_index_name if doc_type == "document" else self.files_index_name
        )
        try:
            self.client.delete(index=index_name, id=doc_id, **self._refresh_kwargs())
        except NotFoundError:
            logger.warning("document_not_found_in_index", doc_id=doc_id, index=index_name)
            return False
        logger.info("document_removed_from_index", doc_id=doc_id, index=index_name)
        return True

    def _bulk_index(self, index_name: str, docs: list[dict[str, Any]], batch_size: int = 500) -> None:
        """Index documents via the bulk API in batches (no per-doc refresh).

        Raises ``RuntimeError`` if any item in a batch fails, matching the
        MeiliSearch adapter's task-wait failure behavior.
        """
        self._ensure_ready()
        for start in range(0, len(docs), batch_size):
            batch = docs[start : start + batch_size]
            actions: list[dict[str, Any]] = []
            for doc in batch:
                actions.append({"index": {"_index": index_name, "_id": doc["id"]}})
                actions.append(doc)
            result = self.client.bulk(body=actions)
            if result.get("errors"):
                failed = [
                    item["index"].get("error")
                    for item in result.get("items", [])
                    if item.get("index", {}).get("error")
                ]
                logger.error(
                    "bulk_index_failed",
                    index=index_name,
                    failed_count=len(failed),
                    first_error=failed[0] if failed else None,
                )
                raise RuntimeError(
                    f"Bulk indexing into '{index_name}' failed for {len(failed)} item(s)"
                )

    def reindex(
        self,
        documents: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Delete indices, recreate them, and optionally repopulate."""
        # Deleting invalidates the bootstrap: if the recreate below fails, later
        # writes must not proceed against auto-created, dynamically mapped indices.
        self._indices_ready = False
        for index_name in [self.documents_index_name, self.files_index_name]:
            try:
                self.client.indices.delete(index=index_name)
                logger.info("opensearch_index_deleted", index=index_name)
            except NotFoundError:
                pass
        self.ensure_indices()

        indexed_counts: dict[str, int] = {"documents": 0, "files": 0}
        if documents:
            self._bulk_index(
                self.documents_index_name,
                [{**doc, "type": "document"} for doc in documents],
            )
            indexed_counts["documents"] = len(documents)
        if files:
            self._bulk_index(
                self.files_index_name,
                [{**file_data, "type": "file"} for file_data in files],
            )
            indexed_counts["files"] = len(files)
        if (documents or files) and self.config.supports_refresh:
            self.client.indices.refresh(
                index=f"{self.documents_index_name},{self.files_index_name}"
            )

        return {
            "status": "reindexed",
            "indices": [self.documents_index_name, self.files_index_name],
            "indexed_counts": indexed_counts,
        }

    def _resolve_indices(self, doc_type: str | None) -> list[str]:
        """Determine which indices to search based on type filter."""
        if doc_type == "document":
            return [self.documents_index_name]
        if doc_type == "file":
            return [self.files_index_name]
        return [self.documents_index_name, self.files_index_name]

    def _parse_hit(self, hit: dict[str, Any], index_name: str) -> SearchHit:
        """Convert an OpenSearch hit to a SearchHit model."""
        source = hit.get("_source", {})
        highlight = hit.get("highlight", {})

        highlights: dict[str, list[str]] = {}
        for field in ["title", "name", "content"]:
            fragments = highlight.get(field)
            if fragments and any("<em>" in str(f) for f in fragments):
                highlights[field] = [str(f) for f in fragments]

        is_doc = index_name == self.documents_index_name
        content_snippet = ""
        if is_doc:
            if highlight.get("content"):
                content_snippet = str(highlight["content"][0])[:200]
            else:
                content_snippet = str(source.get("content", ""))[:200]

        return SearchHit(
            id=source.get("id", hit.get("_id", "")),
            title=source.get("title", "") if is_doc else source.get("name", ""),
            content_snippet=content_snippet,
            type=source.get("type", "document" if is_doc else "file"),
            owner_id=source.get("owner_id", ""),
            tags=source.get("tags", []),
            score=float(hit.get("_score") or 0.0),
            highlights=highlights,
            created_at=source.get("created_at"),
            updated_at=source.get("updated_at"),
            mime_type=source.get("mime_type"),
            folder_id=source.get("folder_id"),
            size=source.get("size"),
        )
