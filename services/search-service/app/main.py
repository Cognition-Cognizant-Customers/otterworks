"""OtterWorks Search Service - Full-text search via MeiliSearch."""

from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from app.api.health import REQUEST_COUNT, REQUEST_LATENCY, health_router
from app.api.index import router as index_router
from app.api.search import router as search_router
from app.config import AppConfig
from app.middleware.auth import AuthMiddleware
from app.services.meilisearch_client import MeiliSearchService

logger = structlog.get_logger()


def configure_logging(log_level: str) -> None:
    """Configure structured JSON logging via structlog."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record request count and latency metrics for every request."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/metrics", "/health"):
            return await call_next(request)
        start = time.monotonic()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            self._record(request, 500, time.monotonic() - start)
            raise
        self._record(request, status, time.monotonic() - start)
        return response

    @staticmethod
    def _endpoint(request: Request) -> str:
        route = request.scope.get("route")
        if route is None:
            for candidate in request.app.routes:
                match, _ = candidate.matches(request.scope)
                if match == Match.FULL:
                    route = candidate
                    break
        path_format = getattr(route, "path_format", None)
        if path_format == "/api/v1/search":
            path_format = "/api/v1/search/"
        return path_format or "unknown"

    @classmethod
    def _record(cls, request: Request, status: int, elapsed: float) -> None:
        endpoint = cls._endpoint(request)
        method = request.method
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
        REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(elapsed)


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = AppConfig()

    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start SQS consumer if enabled
        sqs_consumer = None
        if config.sqs.enabled:
            from app.services.indexer import Indexer
            from app.services.sqs_consumer import SQSConsumer

            indexer = Indexer(app.state.search_service)
            sqs_consumer = SQSConsumer(
                indexer=indexer,
                queue_url=config.sqs.queue_url,
                region=config.sqs.region,
                endpoint_url=config.sqs.endpoint_url,
                max_messages=config.sqs.max_messages,
                wait_time_seconds=config.sqs.wait_time_seconds,
                visibility_timeout=config.sqs.visibility_timeout,
            )
            sqs_consumer.start()
            app.state.sqs_consumer = sqs_consumer
        yield
        if sqs_consumer:
            await sqs_consumer.stop()

    app = FastAPI(
        title="OtterWorks Search Service",
        description="Full-text search and indexing backed by MeiliSearch",
        lifespan=lifespan,
    )

    # Store config on the app
    app.state.app_config = config

    # Initialize MeiliSearch service
    search_service = MeiliSearchService(config.meilisearch)
    app.state.search_service = search_service

    # Try to create indices on startup (non-fatal if MeiliSearch is not available)
    try:
        search_service.ensure_indices()
        logger.info("meilisearch_indices_ensured")
    except Exception:
        logger.warning("meilisearch_indices_creation_deferred", reason="MeiliSearch not available")

    # Register routers
    app.include_router(health_router)
    app.include_router(search_router)
    app.include_router(index_router)

    # Preserve the Flask error envelope for request validation failures.
    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content={"error": "Invalid page or size parameter"})

    # Middleware runs in reverse registration order: CORS -> metrics -> auth.
    app.add_middleware(AuthMiddleware, auth_config=config.auth)
    app.add_middleware(PrometheusMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:4200"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.info(
        "search_service_created",
        port=config.port,
        meilisearch_url=config.meilisearch.url,
        sqs_enabled=config.sqs.enabled,
    )
    return app


if __name__ == "__main__":
    import uvicorn

    app_config = AppConfig()
    uvicorn.run(
        create_app(app_config),
        host=app_config.host,
        port=app_config.port,
        log_level=app_config.log_level.lower(),
    )
