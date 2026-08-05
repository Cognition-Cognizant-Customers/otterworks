"""Search API endpoints."""

from __future__ import annotations

import asyncio
import os

import redis as redis_lib
import structlog
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from app.api.health import SEARCH_COUNT
from app.models.schemas import (
    AnalyticsResponseModel,
    SearchResponseModel,
    SuggestResponseModel,
)
from app.services.meilisearch_client import MeiliSearchService, get_search_analytics

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/search", tags=["search"])

_redis_client: redis_lib.Redis | None = None


def _get_redis() -> redis_lib.Redis:
    """Return a shared Redis client (lazy-initialised)."""
    global _redis_client
    if _redis_client is None:
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        _redis_client = redis_lib.Redis(host=host, port=port, decode_responses=True, socket_timeout=1)
    return _redis_client


def _chaos_active(key: str) -> bool:
    """Return True if the given chaos flag is set in Redis."""
    try:
        return bool(_get_redis().exists(key))
    except Exception:
        return False


def get_search_service(request: Request) -> MeiliSearchService:
    """Get the shared MeiliSearchService from app state."""
    return request.app.state.search_service


@router.get("", include_in_schema=False, response_model=SearchResponseModel, response_model_exclude_none=True)
@router.get("/", response_model=SearchResponseModel, response_model_exclude_none=True)
async def search_documents(
    q: str = Query(""),
    type: str | None = Query(None),
    page: str = Query("1"),
    size: str = Query("20"),
    x_user_id: str | None = Header(None, alias="X-User-ID"),
    service: MeiliSearchService = Depends(get_search_service),
):
    """Full-text search across documents and files.

    Query params: q (required), type, page, size
    Results are automatically scoped to the authenticated user via the
    ``X-User-ID`` header set by the API gateway.
    """
    query = q
    try:
        page_num = max(1, int(page))
        page_size = max(1, min(100, int(size)))
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"error": "Invalid page or size parameter"})
    doc_type = type
    owner_id = x_user_id.strip() if x_user_id and x_user_id.strip() else None

    if not query:
        return JSONResponse(status_code=400, content={"error": "Query parameter 'q' is required"})

    try:
        results = await asyncio.to_thread(
            service.search,
            query=query,
            doc_type=doc_type,
            owner_id=owner_id,
            page=page_num,
            page_size=page_size,
        )
        SEARCH_COUNT.inc()
        logger.info("search_executed", query=query, result_count=results.total)
        return SearchResponseModel(**results.to_dict())
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception:
        logger.exception("search_failed", query=query)
        return JSONResponse(status_code=500, content={"error": "Search failed"})


@router.get("/suggest", response_model=SuggestResponseModel)
async def suggest(
    q: str = Query(""),
    service: MeiliSearchService = Depends(get_search_service),
):
    """Autocomplete suggestions based on prefix.

    Query params: q (required, min 2 chars)
    """
    prefix = q
    if not prefix or len(prefix) < 2:
        return SuggestResponseModel(suggestions=[], query=prefix)

    # CHAOS: when this flag is active the ranking-score enrichment path runs.
    # This path was introduced to sort suggestions by relevance using
    # _rankingScore, but MeiliSearch only returns that field when explicitly
    # requested via attributesToRetrieve — without it the key lookup raises
    # KeyError and crashes the handler with a 500.
    if _chaos_active("chaos:search-service:suggest_500"):
        raw_suggestions = await asyncio.to_thread(service.suggest, prefix)
        if not raw_suggestions:
            # Simulate the same KeyError that fires when results exist but
            # _rankingScore is missing — ensures chaos fires even with an
            # empty index.
            raw_suggestions = [{}]
        # Sort by MeiliSearch ranking score for better relevance ordering.
        ranked = sorted(raw_suggestions, key=lambda s: s["_rankingScore"], reverse=True)  # type: ignore[index]
        return SuggestResponseModel(suggestions=ranked, query=prefix)

    try:
        suggestions = await asyncio.to_thread(service.suggest, prefix)
        return SuggestResponseModel(suggestions=suggestions, query=prefix)
    except Exception:
        logger.exception("suggest_failed", prefix=prefix)
        return SuggestResponseModel(suggestions=[], query=prefix)


@router.post("/advanced", response_model=SearchResponseModel, response_model_exclude_none=True)
async def advanced_search(
    request: Request,
    x_user_id: str | None = Header(None, alias="X-User-ID"),
    service: MeiliSearchService = Depends(get_search_service),
):
    """Advanced search with filters: date range, owner, type, tags.

    JSON body: {q, type, tags, date_from, date_to, page, size}
    owner_id is always derived from X-User-ID for tenant isolation.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    query = data.get("q")
    doc_type = data.get("type")
    owner_id = x_user_id.strip() if x_user_id and x_user_id.strip() else None
    tags = data.get("tags")
    date_from = data.get("date_from")
    date_to = data.get("date_to")
    try:
        page = max(int(data.get("page", 1)), 1)
        page_size = min(max(int(data.get("size", 20)), 1), 100)
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"error": "Invalid page or size parameter"})

    try:
        results = await asyncio.to_thread(
            service.advanced_search,
            query=query,
            doc_type=doc_type,
            owner_id=owner_id,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        SEARCH_COUNT.inc()
        logger.info("advanced_search_executed", query=query, result_count=results.total)
        return SearchResponseModel(**results.to_dict())
    except Exception:
        logger.exception("advanced_search_failed")
        return JSONResponse(status_code=500, content={"error": "Advanced search failed"})


@router.get("/analytics", response_model=AnalyticsResponseModel)
async def search_analytics():
    """Search analytics: popular queries, zero-result queries."""
    try:
        analytics = get_search_analytics()
        return AnalyticsResponseModel(**analytics.to_dict())
    except Exception:
        logger.exception("analytics_failed")
        return JSONResponse(status_code=500, content={"error": "Failed to retrieve analytics"})
