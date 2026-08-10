"""Shared test fixtures."""

import os
import uuid
from collections.abc import AsyncGenerator

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.document import Comment, Document, DocumentVersion, Template  # noqa: F401

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
TEST_JWT_SECRET = "test-jwt-secret-for-unit-tests-pad32"  # noqa: S105
os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)


def auth_header(user_id: uuid.UUID) -> dict[str, str]:
    """Authorization header for a caller acting as ``user_id``."""
    token = jwt.encode({"user_id": str(user_id)}, TEST_JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def owner_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def folder_id() -> uuid.UUID:
    return uuid.uuid4()
