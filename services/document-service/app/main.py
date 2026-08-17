"""OtterWorks Document Service - FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import comments, documents, health, templates
from app.config import settings
from app.db.session import engine, init_db, mongo_store

logger = structlog.get_logger()

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("document_service_starting")
    await init_db()

    if settings.otel_enabled:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(_app)
            logger.info("opentelemetry_instrumented")
        except Exception:
            logger.exception("opentelemetry_setup_failed")

    logger.info("document_service_started")
    yield
    logger.info("document_service_shutting_down")
    await engine.dispose()
    mongo_store.close()


app = FastAPI(
    title="OtterWorks Document Service",
    description="Document CRUD, versioning, and content management",
    version=settings.app_version,
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
app.include_router(comments.router, prefix="/api/v1/documents", tags=["comments"])
app.include_router(templates.router, prefix="/api/v1/templates", tags=["templates"])
