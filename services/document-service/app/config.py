"""Application configuration via pydantic-settings."""

import os
from urllib.parse import quote

from pydantic_settings import BaseSettings


def _default_database_url() -> str:
    """Local-dev database URL assembled from the standard POSTGRES_* env vars."""
    user = quote(os.getenv("POSTGRES_USER", "otterworks"), safe="")
    password = quote(os.getenv("POSTGRES_PASSWORD", "otterworks_dev"), safe="")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "otterworks")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


class Settings(BaseSettings):
    app_name: str = "document-service"
    app_version: str = "0.1.0"
    debug: bool = False

    database_url: str = _default_database_url()
    db_pool_size: int = 10
    db_max_overflow: int = 20

    sns_topic_arn: str = ""
    aws_endpoint_url: str = ""
    aws_region: str = "us-east-1"
    sns_enabled: bool = False

    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_enabled: bool = False

    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:4200"]

    model_config = {"env_prefix": "DOC_SVC_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
