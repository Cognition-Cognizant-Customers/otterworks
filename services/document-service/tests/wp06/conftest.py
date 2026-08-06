"""Fixtures for the WP-06 coverage suite.

Everything here is additive: the parent ``tests/conftest.py`` fixtures
(``setup_db``, ``db_session``, ``client``) still apply. No fixture in this module
is autouse and none holds mutable state across tests, so ordering never matters.
"""

import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base

# Long enough to satisfy PyJWT's HS384 key-length guidance, so signing/verifying
# never emits an InsecureKeyLengthWarning.
WP06_JWT_SECRET = "wp06-document-service-test-secret-key-0123456789abcdef"  # noqa: S105

MAX_TITLE_LENGTH = 500
MAX_PAGE_SIZE = 100


def make_jwt(user_id: uuid.UUID | str, algorithm: str = "HS256") -> str:
    """Sign a JWT the document-service will accept for ``user_id``."""
    return jwt.encode({"user_id": str(user_id)}, WP06_JWT_SECRET, algorithm=algorithm)


def auth_headers(user_id: uuid.UUID | str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_jwt(user_id)}"}


@pytest.fixture
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin ``JWT_SECRET`` for the duration of a test.

    The API reads the secret from the environment on every request, so setting it
    explicitly (rather than relying on another test module's import-time
    ``setdefault``) keeps these tests independent of collection order.
    """
    monkeypatch.setenv("JWT_SECRET", WP06_JWT_SECRET)
    return WP06_JWT_SECRET


@pytest.fixture
def user_a() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def user_b() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def create_document(
    client: AsyncClient, jwt_secret: str
) -> Callable[..., Any]:
    """Return an async helper that creates a document owned by ``owner``."""

    async def _create(
        owner: uuid.UUID,
        title: str = "WP-06 Document",
        content: str = "body text",
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "content": content,
            "owner_id": str(owner),
            **extra,
        }
        resp = await client.post("/api/v1/documents/", json=payload)
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _create


@pytest.fixture
async def file_engine(tmp_path) -> AsyncGenerator[AsyncEngine, None]:
    """A file-backed SQLite engine so several sessions get distinct connections.

    The in-memory engine used by the parent conftest is backed by a single shared
    connection, which cannot express two independent sessions.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/wp06.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def file_sessionmaker(file_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(file_engine, class_=AsyncSession, expire_on_commit=False)
