"""Shared test fixtures."""

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from pymongo import MongoClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_MONGO_DB = f"ow_tp_document_tests_{uuid.uuid4().hex}"
TEST_MONGO_URI = os.environ.get("DOC_SVC_MONGO_URI", "mongodb://localhost:27017")
os.environ["DOC_SVC_MONGO_URI"] = TEST_MONGO_URI
os.environ["DOC_SVC_MONGO_DB"] = TEST_MONGO_DB
os.environ["DOC_SVC_NAMESPACE"] = "test"

from app.db.base import Base  # noqa: E402
from app.db.session import get_db, mongo_store  # noqa: E402
from app.main import app  # noqa: E402
from app.models.document import Comment, Document, DocumentVersion, Template  # noqa: E402,F401

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture(autouse=True)
async def setup_db():
    mongo_store.documents.delete_many({"ns": "test"})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="session", autouse=True)
async def cleanup_mongo():
    yield
    client = MongoClient(TEST_MONGO_URI)
    client.drop_database(TEST_MONGO_DB)
    client.close()


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
