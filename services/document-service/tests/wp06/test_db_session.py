"""Coverage for app/db/session.py: engine wiring, init_db, and get_db lifecycle.

The module-level engine points at Postgres, so every test here swaps in a
file-backed SQLite engine via monkeypatch. Nothing touches a real database.
"""

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db import session as session_module
from app.db.base import Base
from app.models.document import Document

pytestmark = pytest.mark.asyncio


def _document(owner: uuid.UUID, title: str) -> Document:
    return Document(title=title, content="body", owner_id=owner, word_count=1)


async def test_module_engine_is_built_from_settings():
    assert isinstance(session_module.engine, AsyncEngine)
    assert session_module.engine.url.render_as_string(hide_password=False) == (
        settings.database_url
    )
    assert session_module.async_session.kw["expire_on_commit"] is False


async def test_init_db_creates_every_table(monkeypatch: pytest.MonkeyPatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/init.db")
    monkeypatch.setattr(session_module, "engine", engine)
    try:
        await session_module.init_db()
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    finally:
        await engine.dispose()

    assert set(Base.metadata.tables) <= tables


async def test_init_db_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/init-twice.db")
    monkeypatch.setattr(session_module, "engine", engine)
    try:
        await session_module.init_db()
        await session_module.init_db()
    finally:
        await engine.dispose()


async def test_get_db_yields_a_usable_session_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
    file_sessionmaker: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
):
    closed: list[str] = []

    class TrackingSession(AsyncSession):
        async def close(self) -> None:
            closed.append("closed")
            await super().close()

    tracking = async_sessionmaker(
        file_sessionmaker.kw["bind"], class_=TrackingSession, expire_on_commit=False
    )
    monkeypatch.setattr(session_module, "async_session", tracking)

    generator = session_module.get_db()
    session = await anext(generator)
    assert isinstance(session, AsyncSession)
    session.add(_document(owner_id, "committed"))
    await session.commit()

    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    # get_db closes explicitly in its finally block and again when the
    # `async with async_session()` context exits; close() is idempotent.
    assert closed == ["closed", "closed"]

    async with file_sessionmaker() as verify:
        rows = await verify.execute(select(Document.title))
        assert rows.scalars().all() == ["committed"]


async def test_get_db_rolls_back_uncommitted_work_on_exception(
    monkeypatch: pytest.MonkeyPatch,
    file_sessionmaker: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
):
    monkeypatch.setattr(session_module, "async_session", file_sessionmaker)

    generator = session_module.get_db()
    session = await anext(generator)
    session.add(_document(owner_id, "never persisted"))
    await session.flush()

    with pytest.raises(RuntimeError, match="handler blew up"):
        await generator.athrow(RuntimeError("handler blew up"))

    async with file_sessionmaker() as verify:
        rows = await verify.execute(select(Document.title))
        assert rows.scalars().all() == []


async def test_get_db_keeps_work_committed_before_an_exception(
    monkeypatch: pytest.MonkeyPatch,
    file_sessionmaker: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
):
    monkeypatch.setattr(session_module, "async_session", file_sessionmaker)

    generator = session_module.get_db()
    session = await anext(generator)
    session.add(_document(owner_id, "already committed"))
    await session.commit()
    session.add(_document(owner_id, "still pending"))
    await session.flush()

    with pytest.raises(RuntimeError):
        await generator.athrow(RuntimeError("handler blew up"))

    async with file_sessionmaker() as verify:
        rows = await verify.execute(select(Document.title))
        assert rows.scalars().all() == ["already committed"]


async def test_get_db_hands_out_a_new_session_per_request(
    monkeypatch: pytest.MonkeyPatch, file_sessionmaker: async_sessionmaker[AsyncSession]
):
    monkeypatch.setattr(session_module, "async_session", file_sessionmaker)

    first_gen, second_gen = session_module.get_db(), session_module.get_db()
    first, second = await anext(first_gen), await anext(second_gen)
    assert first is not second

    for generator in (first_gen, second_gen):
        with pytest.raises(StopAsyncIteration):
            await anext(generator)


async def test_session_is_closed_even_when_the_caller_raises(
    monkeypatch: pytest.MonkeyPatch, file_sessionmaker: async_sessionmaker[AsyncSession]
):
    closed: list[str] = []

    class TrackingSession(AsyncSession):
        async def close(self) -> None:
            closed.append("closed")
            await super().close()

    tracking = async_sessionmaker(
        file_sessionmaker.kw["bind"], class_=TrackingSession, expire_on_commit=False
    )
    monkeypatch.setattr(session_module, "async_session", tracking)

    generator = session_module.get_db()
    await anext(generator)
    with pytest.raises(ValueError, match="boom"):
        await generator.athrow(ValueError("boom"))

    assert closed == ["closed", "closed"]
