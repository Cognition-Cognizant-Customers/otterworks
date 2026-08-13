"""Runtime configuration for the serverless ETL.

Non-sensitive settings come from environment variables (set by Terraform on
each Lambda). Credentials come from AWS Secrets Manager; the Lambda execution
role grants access, so no keys ever appear in code or config files.
"""

import json
import os
import time
from dataclasses import dataclass
from functools import cache

import boto3

# secrets are re-fetched after this long so a warm Lambda environment picks
# up rotated credentials without waiting to be recycled
SECRET_TTL_SECONDS = 300


def env(name: str, default: str | None = None) -> str:
    # empty strings count as missing: Terraform always sets the variable,
    # so an unconfigured value arrives as "" rather than being absent
    value = os.environ.get(name) or default
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@cache
def client(service: str):
    return boto3.client(service, region_name=os.environ.get("AWS_REGION", "us-east-1"))


@cache
def resource(service: str):
    return boto3.resource(service, region_name=os.environ.get("AWS_REGION", "us-east-1"))


_secret_cache: dict[str, tuple[float, dict]] = {}


def get_secret(secret_id: str) -> dict:
    cached = _secret_cache.get(secret_id)
    if cached and time.monotonic() - cached[0] < SECRET_TTL_SECONDS:
        return cached[1]
    response = client("secretsmanager").get_secret_value(SecretId=secret_id)
    secret = json.loads(response["SecretString"])
    _secret_cache[secret_id] = (time.monotonic(), secret)
    return secret


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


def database_config() -> DatabaseConfig:
    secret = get_secret(env("DB_SECRET_ID"))
    return DatabaseConfig(
        host=secret["host"],
        port=int(secret.get("port", 5432)),
        database=secret["database"],
        user=secret["username"],
        password=secret["password"],
    )


def meilisearch_api_key() -> str:
    return get_secret(env("MEILISEARCH_SECRET_ID"))["api_key"]
