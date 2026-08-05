"""Health check and metrics endpoints."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
)

from app.models.schemas import HealthResponseModel, ReadinessResponseModel

logger = structlog.get_logger()

health_router = APIRouter()

# Prometheus metrics
REQUEST_COUNT = Counter(
    "search_service_requests_total",
    "Total number of requests to the search service",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "search_service_request_duration_seconds",
    "Request latency in seconds",
    ["method", "endpoint"],
)
SEARCH_COUNT = Counter(
    "search_service_searches_total",
    "Total number of search queries executed",
)
INDEX_COUNT = Counter(
    "search_service_index_operations_total",
    "Total number of index operations",
    ["operation", "type"],
)


@health_router.get("/health", response_model=HealthResponseModel)
async def health() -> HealthResponseModel:
    """Liveness check — returns 200 if the process is running."""
    return HealthResponseModel(status="alive", service="search-service")


@health_router.get("/health/ready")
async def readiness(request: Request):
    """Readiness check — returns 503 if MeiliSearch is unreachable."""
    search_service = getattr(request.app.state, "search_service", None)

    healthy = False
    if search_service:
        healthy = await asyncio.to_thread(search_service.ping)

    if healthy:
        return ReadinessResponseModel(ready=True)
    return JSONResponse(
        status_code=503,
        content={"ready": False, "reason": "meilisearch_unavailable"},
    )


@health_router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type="text/plain; charset=utf-8",
    )
