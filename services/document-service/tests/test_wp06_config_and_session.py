"""WP-06: ``app.config`` defaults/validation and ``app.db.session`` wiring.

``Settings`` is always constructed with ``_env_file=None`` so a stray ``.env``
in the working directory cannot change a result, and every case wipes the
``DOC_SVC_`` namespace first so the tests do not depend on the ambient
environment or on each other.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, settings
from app.db import session as session_module
from app.db.base import Base

ENV_PREFIX = "DOC_SVC_"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in list(os.environ):
        if name.startswith(ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ------------------------------------------------------------ config: defaults --


def test_defaults_are_the_documented_local_development_values(clean_env: Any):
    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.app_name == "document-service"
    assert config.debug is False
    assert config.db_pool_size == 10
    assert config.db_max_overflow == 20
    assert config.aws_region == "us-east-1"
    assert config.otel_enabled is False
    assert config.cors_origins == [
        "http://localhost:3000",
        "http://localhost:4200",
    ]


def test_event_publishing_is_off_by_default(clean_env: Any):
    """The publisher's kill switch defaults closed, with no topic configured."""
    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.sns_enabled is False
    assert config.sns_topic_arn == ""
    assert config.aws_endpoint_url == ""


def test_the_database_url_defaults_to_the_local_compose_postgres(clean_env: Any):
    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.database_url.startswith("postgresql+asyncpg://")


# --------------------------------------------------------- config: overrides --


def test_environment_values_override_defaults(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("DOC_SVC_APP_NAME", "doc-svc-override")
    clean_env.setenv("DOC_SVC_DB_POOL_SIZE", "42")
    clean_env.setenv("DOC_SVC_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:0:docs")

    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.app_name == "doc-svc-override"
    assert config.db_pool_size == 42
    assert config.sns_topic_arn == "arn:aws:sns:us-east-1:0:docs"


def test_unprefixed_variables_are_ignored(clean_env: pytest.MonkeyPatch):
    """``env_prefix`` isolates the service from generic names like ``DEBUG``."""
    clean_env.setenv("APP_NAME", "not-the-document-service")
    clean_env.setenv("DEBUG", "true")

    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.app_name == "document-service"
    assert config.debug is False


def test_the_prefix_is_case_insensitive(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("doc_svc_app_name", "lowercase-wins")

    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.app_name == "lowercase-wins"


def test_unknown_prefixed_variables_are_ignored_not_rejected(
    clean_env: pytest.MonkeyPatch,
):
    """``extra="ignore"`` means a stale variable cannot crash start-up."""
    clean_env.setenv("DOC_SVC_A_SETTING_THAT_NO_LONGER_EXISTS", "1")

    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.app_name == "document-service"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("True", True), ("1", True), ("false", False), ("0", False)],
)
def test_boolean_flags_accept_the_usual_spellings(
    clean_env: pytest.MonkeyPatch, raw: str, expected: bool
):
    clean_env.setenv("DOC_SVC_SNS_ENABLED", raw)

    assert Settings(_env_file=None).sns_enabled is expected  # type: ignore[call-arg]


def test_cors_origins_are_parsed_as_a_json_list(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("DOC_SVC_CORS_ORIGINS", '["https://app.example.com"]')

    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.cors_origins == ["https://app.example.com"]


def test_an_empty_cors_list_is_accepted(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("DOC_SVC_CORS_ORIGINS", "[]")

    assert Settings(_env_file=None).cors_origins == []  # type: ignore[call-arg]


# --------------------------------------------------------- config: negative --


@pytest.mark.parametrize("raw", ["maybe", "", "2", "yes-please"])
def test_an_invalid_boolean_is_rejected(clean_env: pytest.MonkeyPatch, raw: str):
    clean_env.setenv("DOC_SVC_DEBUG", raw)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("raw", ["abc", "10.5", ""])
def test_a_non_integer_pool_size_is_rejected(clean_env: pytest.MonkeyPatch, raw: str):
    clean_env.setenv("DOC_SVC_DB_POOL_SIZE", raw)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_a_non_positive_pool_size_is_currently_accepted(clean_env: pytest.MonkeyPatch, raw: str):
    """Pinned gap: ``db_pool_size`` has no lower bound, so 0 and -1 validate.

    SQLAlchemy would only fail later, at engine construction. Recorded rather
    than fixed — adding a constraint is a production change.
    """
    clean_env.setenv("DOC_SVC_DB_POOL_SIZE", raw)

    assert Settings(_env_file=None).db_pool_size == int(raw)  # type: ignore[call-arg]


def test_malformed_cors_json_is_rejected(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("DOC_SVC_CORS_ORIGINS", "not-json")

    with pytest.raises(Exception, match="(?i)cors_origins"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_module_level_settings_singleton_is_a_settings_instance():
    assert isinstance(settings, Settings)


# ----------------------------------------------------------- db/session --


@pytest.fixture
async def sqlite_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/session.db")
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_creates_every_declared_table(sqlite_engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(session_module, "engine", sqlite_engine)

    await session_module.init_db()

    async with sqlite_engine.connect() as conn:
        names = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
    assert {"documents", "document_versions", "comments", "templates"} <= set(names)


@pytest.mark.asyncio
async def test_init_db_is_idempotent(sqlite_engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(session_module, "engine", sqlite_engine)

    await session_module.init_db()
    await session_module.init_db()

    async with sqlite_engine.connect() as conn:
        names = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
    assert len(names) == len(set(names))


@pytest.mark.asyncio
async def test_get_db_yields_a_usable_session_and_closes_it(
    sqlite_engine, monkeypatch: pytest.MonkeyPatch
):
    closed: list[str] = []

    class RecordingSession(AsyncSession):
        async def close(self) -> None:
            closed.append("closed")
            await super().close()

    async with sqlite_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(
        session_module,
        "async_session",
        async_sessionmaker(sqlite_engine, class_=RecordingSession),
    )

    generator: AsyncGenerator[AsyncSession, None] = session_module.get_db()
    session = await anext(generator)
    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1

    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    # Twice: once from the explicit ``finally`` and once from the
    # ``async with async_session()`` block that wraps it. Closing is
    # idempotent, so this is redundant rather than wrong.
    assert closed == ["closed", "closed"]


@pytest.mark.asyncio
async def test_get_db_closes_the_session_when_the_caller_raises(
    sqlite_engine, monkeypatch: pytest.MonkeyPatch
):
    closed: list[str] = []

    class RecordingSession(AsyncSession):
        async def close(self) -> None:
            closed.append("closed")
            await super().close()

    monkeypatch.setattr(
        session_module,
        "async_session",
        async_sessionmaker(sqlite_engine, class_=RecordingSession),
    )

    generator = session_module.get_db()
    await anext(generator)

    with pytest.raises(RuntimeError):
        await generator.athrow(RuntimeError("handler blew up"))
    assert closed == ["closed", "closed"]


def test_the_engine_is_configured_from_settings():
    assert session_module.engine.pool.size() == settings.db_pool_size
    assert str(session_module.engine.url).startswith(settings.database_url.split("://", 1)[0])


def test_sessions_do_not_expire_attributes_on_commit():
    """ORM objects are read after ``commit()`` in the service layer, so
    ``expire_on_commit`` must stay off or every response would re-query."""
    assert session_module.async_session.kw["expire_on_commit"] is False
