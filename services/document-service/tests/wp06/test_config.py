"""Coverage for app/config.py: defaults, env overrides and invalid values."""

import os

import pytest
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from app.config import Settings, settings

ENV_PREFIX = "DOC_SVC_"


def _fresh() -> Settings:
    """Build Settings without reading a developer's local .env file."""
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any ambient DOC_SVC_* variables so defaults are observable."""
    for key in list(os.environ):
        if key.upper().startswith(ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)


def test_defaults_when_no_environment_is_set(clean_env: None):
    fresh = _fresh()
    assert fresh.app_name == "document-service"
    assert fresh.app_version == "0.1.0"
    assert fresh.debug is False
    assert fresh.db_pool_size == 10
    assert fresh.db_max_overflow == 20
    assert fresh.sns_enabled is False
    assert fresh.sns_topic_arn == ""
    assert fresh.aws_endpoint_url == ""
    assert fresh.aws_region == "us-east-1"
    assert fresh.otel_enabled is False
    assert fresh.otel_exporter_otlp_endpoint == "http://localhost:4317"
    assert fresh.cors_origins == ["http://localhost:3000", "http://localhost:4200"]
    assert fresh.database_url.startswith("postgresql+asyncpg://")


def test_module_level_settings_is_a_settings_instance():
    assert isinstance(settings, Settings)


def test_env_prefix_is_required(clean_env: None, monkeypatch: pytest.MonkeyPatch):
    """An unprefixed variable must not leak into configuration."""
    monkeypatch.setenv("APP_NAME", "hijacked")
    assert _fresh().app_name == "document-service"


def test_scalar_overrides_are_applied(clean_env: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DOC_SVC_APP_NAME", "doc-svc-override")
    monkeypatch.setenv("DOC_SVC_DB_POOL_SIZE", "42")
    monkeypatch.setenv("DOC_SVC_AWS_REGION", "eu-west-1")
    monkeypatch.setenv("DOC_SVC_SNS_TOPIC_ARN", "arn:aws:sns:eu-west-1:1:docs")

    fresh = _fresh()
    assert fresh.app_name == "doc-svc-override"
    assert fresh.db_pool_size == 42
    assert fresh.aws_region == "eu-west-1"
    assert fresh.sns_topic_arn == "arn:aws:sns:eu-west-1:1:docs"


def test_env_var_names_are_case_insensitive(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("doc_svc_app_name", "lowercase-wins")
    assert _fresh().app_name == "lowercase-wins"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("True", True), ("1", True), ("false", False), ("0", False)],
)
def test_boolean_overrides(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
):
    monkeypatch.setenv("DOC_SVC_SNS_ENABLED", raw)
    assert _fresh().sns_enabled is expected


def test_list_override_is_parsed_as_json(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DOC_SVC_CORS_ORIGINS", '["https://a.example", "https://b.example"]')
    assert _fresh().cors_origins == ["https://a.example", "https://b.example"]


def test_empty_list_override(clean_env: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DOC_SVC_CORS_ORIGINS", "[]")
    assert _fresh().cors_origins == []


@pytest.mark.parametrize("raw", ["not-a-number", "", "10.5", "ten"])
def test_invalid_integer_override_is_rejected(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, raw: str
):
    monkeypatch.setenv("DOC_SVC_DB_POOL_SIZE", raw)
    with pytest.raises(ValidationError):
        _fresh()


@pytest.mark.parametrize("raw", ["maybe", "2", "yes-please"])
def test_invalid_boolean_override_is_rejected(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, raw: str
):
    monkeypatch.setenv("DOC_SVC_DEBUG", raw)
    with pytest.raises(ValidationError):
        _fresh()


def test_invalid_list_override_is_rejected(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DOC_SVC_CORS_ORIGINS", "{not json")
    with pytest.raises(SettingsError):
        _fresh()


def test_negative_pool_size_is_accepted_by_config(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
):
    """Pins current behaviour: pool sizes carry no lower bound in the schema."""
    monkeypatch.setenv("DOC_SVC_DB_POOL_SIZE", "-1")
    assert _fresh().db_pool_size == -1


def test_unknown_prefixed_variables_are_ignored(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
):
    """extra="ignore" means a typo'd variable is dropped rather than fatal."""
    monkeypatch.setenv("DOC_SVC_TOTALLY_UNKNOWN", "value")
    fresh = _fresh()
    assert not hasattr(fresh, "totally_unknown")


def test_zero_pool_size_boundary(clean_env: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DOC_SVC_DB_POOL_SIZE", "0")
    monkeypatch.setenv("DOC_SVC_DB_MAX_OVERFLOW", "0")
    fresh = _fresh()
    assert fresh.db_pool_size == 0
    assert fresh.db_max_overflow == 0
