"""Runtime configuration for the serverless ETL.

Non-sensitive settings come from environment variables (set by Terraform on
each Lambda). Credentials come from AWS Secrets Manager; the Lambda execution
role grants access, so no keys ever appear in code or config files.
"""

import json
import os
from dataclasses import dataclass
from functools import cache

import boto3


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@cache
def client(service: str):
    return boto3.client(service, region_name=os.environ.get("AWS_REGION", "us-east-1"))


@cache
def resource(service: str):
    return boto3.resource(service, region_name=os.environ.get("AWS_REGION", "us-east-1"))


@cache
def get_secret(secret_id: str) -> dict:
    response = client("secretsmanager").get_secret_value(SecretId=secret_id)
    return json.loads(response["SecretString"])


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
