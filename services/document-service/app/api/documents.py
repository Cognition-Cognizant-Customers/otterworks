"""Document CRUD API endpoints."""

import asyncio
import os
import random
from uuid import UUID

import jwt
import redis as redis_lib
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.document import (
    DocumentCreate,
    DocumentFromTemplate,
    DocumentListResponse,
    DocumentPatch,
    DocumentResponse,
    DocumentUpdate,
    DocumentVersionResponse,
)
from app.services.document_service import DocumentService

logger = structlog.get_logger()
router = APIRouter()

_redis_client: redis_lib.Redis | None = None


def _get_redis() -> redis_lib.Redis:
    """Return a shared Redis client (lazy-initialised)."""
    global _redis_client
    if _redis_client is None:
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        _redis_client = redis_lib.Redis(
            host=host, port=port, decode_responses=True, socket_timeout=1,
        )
    return _redis_client


def _chaos_active(key: str) -> bool:
    """Return True if the given chaos flag is set in Redis."""
    try:
        return bool(_get_redis().exists(key))
    except Exception:
        return False


async def _maybe_inject_latency() -> None:
    """Inject 3-5s delay when the slow_queries chaos flag is active."""
    if _chaos_active("chaos:document-service:slow_queries"):
        delay = random.uniform(3.0, 5.0)
        logger.warning("chaos_latency_injected", delay_seconds=round(delay, 2))
        await asyncio.sleep(delay)


def _get_jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "")


def _extract_user_id(request: Request) -> UUID | None:
    """Extract user ID from the Authorization JWT."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        secret = _get_jwt_secret()
        if secret:
            try:
                payload = jwt.decode(token, secret, algorithms=["HS256", "HS384"])
                user_id_str = payload.get("user_id") or payload.get("sub")
                if user_id_str:
                    return UUID(str(user_id_str))
            except (jwt.PyJWTError, ValueError):
                pass
        else:
            forwarded_user_id = request.headers.get("X-User-ID")
            if forwarded_user_id:
                try:
                    return UUID(str(forwarded_user_id))
                except ValueError:
                    pass

    return None


def _require_user_id(request: Request) -> UUID:
    user_id = _extract_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def _ensure_owner(document: object, user_id: UUID) -> None:
    if getattr(document, "owner_id", None) != user_id:
        raise HTTPException(status_code=403, detail="Access denied")


async def _do_create_document(
    body: DocumentCreate,
    request: Request,
    db: AsyncSession,
) -> DocumentResponse:
    if not body.owner_id:
        extracted_id = _extract_user_id(request)
        if not extracted_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="owner_id is required: provide it in the body or authenticate via JWT",
            )
        body.owner_id = extracted_id

    service = DocumentService(db)
    document = await service.create(body)
    logger.info("document_created", document_id=str(document.id))
    return document


@router.post(
    "/",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={401: {"description": "Authentication required"}},
)
async def create_document(
    body: DocumentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new document."""
    await _maybe_inject_latency()
    return await _do_create_document(body, request, db)


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
    responses={401: {"description": "Authentication required"}},
)
async def create_document_no_slash(
    body: DocumentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new document (no trailing slash)."""
    await _maybe_inject_latency()
    return await _do_create_document(body, request, db)


@router.get("/search", response_model=DocumentListResponse)
async def search_documents(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Search documents by title or content."""
    await _maybe_inject_latency()
    service = DocumentService(db)
    items, total = await service.search(q, page=page, size=size)
    return DocumentListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=service.paginate(total, page, size),
    )


async def _do_list_documents(
    owner_id: UUID | None,
    folder_id: UUID | None,
    page: int,
    size: int,
    db: AsyncSession,
) -> DocumentListResponse:
    await _maybe_inject_latency()
    service = DocumentService(db)
    items, total = await service.list_documents(
        owner_id=owner_id, folder_id=folder_id, page=page, size=size
    )
    return DocumentListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=service.paginate(total, page, size),
    )


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    request: Request,
    owner_id: UUID | None = None,
    folder_id: UUID | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List documents with optional filtering and pagination."""
    effective_owner = owner_id or _extract_user_id(request)
    return await _do_list_documents(effective_owner, folder_id, page, size, db)


@router.get(
    "",
    response_model=DocumentListResponse,
    include_in_schema=False,
)
async def list_documents_no_slash(
    request: Request,
    owner_id: UUID | None = None,
    folder_id: UUID | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List documents (no trailing slash)."""
    effective_owner = owner_id or _extract_user_id(request)
    return await _do_list_documents(effective_owner, folder_id, page, size, db)


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Access denied"},
        404: {"description": "Document not found"},
    },
)
async def get_document(
    document_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get a document by ID."""
    await _maybe_inject_latency()
    user_id = _require_user_id(request)
    service = DocumentService(db)
    document = await service.get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    _ensure_owner(document, user_id)
    return document


@router.put(
    "/{document_id}",
    response_model=DocumentResponse,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Access denied"},
        404: {"description": "Document not found"},
    },
)
async def update_document(
    document_id: UUID,
    body: DocumentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Full replace of a document."""
    await _maybe_inject_latency()
    user_id = _require_user_id(request)
    service = DocumentService(db)
    existing = await service.get(document_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")
    _ensure_owner(existing, user_id)
    document = await service.update(document_id, body)
    logger.info("document_updated", document_id=str(document_id))
    return document


@router.patch(
    "/{document_id}",
    response_model=DocumentResponse,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Access denied"},
        404: {"description": "Document not found"},
    },
)
async def patch_document(
    document_id: UUID,
    body: DocumentPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Partial update of a document."""
    await _maybe_inject_latency()
    user_id = _require_user_id(request)
    service = DocumentService(db)
    existing = await service.get(document_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")
    _ensure_owner(existing, user_id)
    document = await service.patch(document_id, body)
    logger.info("document_patched", document_id=str(document_id))
    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Access denied"},
        404: {"description": "Document not found"},
    },
)
async def delete_document(
    document_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a document (soft delete)."""
    await _maybe_inject_latency()
    user_id = _require_user_id(request)
    service = DocumentService(db)
    existing = await service.get(document_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")
    _ensure_owner(existing, user_id)
    await service.delete(document_id)
    logger.info("document_deleted", document_id=str(document_id))


@router.get(
    "/{document_id}/versions",
    response_model=list[DocumentVersionResponse],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Access denied"},
        404: {"description": "Document not found"},
    },
)
async def list_versions(
    document_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List document versions."""
    user_id = _require_user_id(request)
    service = DocumentService(db)
    document = await service.get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    _ensure_owner(document, user_id)
    versions = await service.list_versions(document_id)
    return versions


@router.post(
    "/{document_id}/versions/{version_id}/restore",
    response_model=DocumentResponse,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Access denied"},
        404: {"description": "Document or version not found"},
    },
)
async def restore_version(
    document_id: UUID,
    version_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Restore a document to a previous version."""
    user_id = _require_user_id(request)
    service = DocumentService(db)
    existing = await service.get(document_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Document or version not found")
    _ensure_owner(existing, user_id)
    document = await service.restore_version(document_id, version_id)
    if not document:
        raise HTTPException(
            status_code=404, detail="Document or version not found"
        )
    logger.info(
        "document_version_restored",
        document_id=str(document_id),
        version_id=str(version_id),
    )
    return document


@router.get(
    "/{document_id}/export",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Access denied"},
        404: {"description": "Document not found"},
    },
)
async def export_document(
    document_id: UUID,
    request: Request,
    format: str = Query("markdown", pattern="^(pdf|html|markdown)$"),  # noqa: A002
    db: AsyncSession = Depends(get_db),
):
    """Export a document in the requested format."""
    user_id = _require_user_id(request)
    service = DocumentService(db)
    document = await service.get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    _ensure_owner(document, user_id)

    body, content_type = service.export_document(document, format)
    return PlainTextResponse(content=body, media_type=content_type)


@router.post(
    "/from-template/{template_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"description": "Template not found"}},
)
async def create_from_template(
    template_id: UUID,
    body: DocumentFromTemplate,
    db: AsyncSession = Depends(get_db),
):
    """Create a document from a template."""
    service = DocumentService(db)
    document = await service.create_from_template(template_id, body)
    if not document:
        raise HTTPException(status_code=404, detail="Template not found")
    logger.info(
        "document_created_from_template",
        document_id=str(document.id),
        template_id=str(template_id),
    )
    return document
