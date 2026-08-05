"""Indexing API endpoints for documents and files."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.health import INDEX_COUNT
from app.api.search import get_search_service
from app.models.schemas import IndexResponseModel
from app.services.indexer import Indexer
from app.services.meilisearch_client import MeiliSearchService

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/search", tags=["index"])


def get_indexer(
    search_service: MeiliSearchService = Depends(get_search_service),
) -> Indexer:
    """Get an Indexer instance backed by the shared MeiliSearchService."""
    return Indexer(search_service)


async def _read_json_body(request: Request) -> dict | None:
    """Read the request body as JSON, returning None when absent or invalid."""
    try:
        data = await request.json()
    except Exception:
        return None
    if not isinstance(data, dict) or not data:
        return None
    return data


@router.post("/index/document", status_code=201, response_model=IndexResponseModel)
async def index_document(request: Request, indexer: Indexer = Depends(get_indexer)):
    """Index a document (called by document-service or SQS)."""
    data = await _read_json_body(request)
    if not data:
        return JSONResponse(status_code=400, content={"error": "Request body is required"})

    try:
        result = await asyncio.to_thread(indexer.index_document, data)
        INDEX_COUNT.labels(operation="index", type="document").inc()
        logger.info("api_document_indexed", document_id=data.get("id"))
        return IndexResponseModel(**result)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception:
        logger.exception("api_index_document_failed")
        return JSONResponse(status_code=500, content={"error": "Failed to index document"})


@router.post("/index/file", status_code=201, response_model=IndexResponseModel)
async def index_file(request: Request, indexer: Indexer = Depends(get_indexer)):
    """Index a file (called by file-service or SQS)."""
    data = await _read_json_body(request)
    if not data:
        return JSONResponse(status_code=400, content={"error": "Request body is required"})

    try:
        result = await asyncio.to_thread(indexer.index_file, data)
        INDEX_COUNT.labels(operation="index", type="file").inc()
        logger.info("api_file_indexed", file_id=data.get("id"))
        return IndexResponseModel(**result)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception:
        logger.exception("api_index_file_failed")
        return JSONResponse(status_code=500, content={"error": "Failed to index file"})


@router.delete("/index/{doc_type}/{doc_id}", response_model=IndexResponseModel)
async def remove_from_index(
    doc_type: str, doc_id: str, indexer: Indexer = Depends(get_indexer)
):
    """Remove a document or file from the search index."""
    try:
        result = await asyncio.to_thread(indexer.remove, doc_type, doc_id)
        if result["status"] == "not_found":
            return JSONResponse(status_code=404, content=result)
        INDEX_COUNT.labels(operation="delete", type=doc_type).inc()
        logger.info("api_document_removed", doc_type=doc_type, doc_id=doc_id)
        return IndexResponseModel(**result)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception:
        logger.exception("api_remove_from_index_failed")
        return JSONResponse(status_code=500, content={"error": "Failed to remove from index"})


@router.post("/reindex")
async def reindex(indexer: Indexer = Depends(get_indexer)):
    """Reindex all data (admin operation)."""
    try:
        result = await asyncio.to_thread(indexer.reindex)
        logger.info("api_reindex_triggered")
        return result
    except Exception:
        logger.exception("api_reindex_failed")
        return JSONResponse(status_code=500, content={"error": "Failed to reindex"})
