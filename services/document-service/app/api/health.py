"""Health check and metrics endpoints."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

logger = structlog.get_logger()
router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/health")
async def health(db: DbSession):
    """Health check with DB connectivity verification."""
    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        logger.exception("health_check_db_failed")
        db_status = "disconnected"

    status = "healthy" if db_status == "connected" else "degraded"
    return {
        "status": status,
        "service": "document-service",
        "checks": {"database": db_status},
    }


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus metrics endpoint."""
    return (
        "# HELP document_service_up Document Service is running\n"
        "# TYPE document_service_up gauge\n"
        "document_service_up 1\n"
    )
