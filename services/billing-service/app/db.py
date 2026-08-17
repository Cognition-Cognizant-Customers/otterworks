from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from bson import Decimal128
from psycopg.rows import dict_row
from pymongo import MongoClient

from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "migrations" / "001_initial.sql"
SEED = ROOT / "db" / "seed.sql"
MONGO_SEED = ROOT / "db" / "mongo_seed.json"
_MONGO_CLIENT: MongoClient | None = None


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def mongo_client() -> MongoClient:
    global _MONGO_CLIENT
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = MongoClient(
            settings.mongo_uri,
            tz_aware=True,
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=1000,
        )
    return _MONGO_CLIENT


def mongo_database():
    return mongo_client()[settings.mongo_db]


def ensure_rating_indexes() -> None:
    mongo_database().rating_periods.create_index(
        [("tenant_id", 1), ("period_start", 1)],
        unique=True,
        name="tenant_period_start_unique",
    )


def migrate() -> None:
    with connect() as connection:
        connection.execute(MIGRATION.read_text())


def reset() -> None:
    with connect() as connection:
        connection.execute(MIGRATION.read_text())
        connection.execute(
            """
            TRUNCATE TABLE billing_svc.subscriptions,
                           billing_svc.plans,
                           billing_svc.tenants
            """
        )
        connection.execute(SEED.read_text())
    database = mongo_database()
    database.drop_collection("usage_events")
    database.drop_collection("rating_periods")
    seed = json.loads(MONGO_SEED.read_text())
    database["usage_events"].insert_many(
        [
            {
                **event,
                "_id": UUID(event["_id"]),
                "tenant_id": UUID(event["tenant_id"]),
                "occurred_at": _utc_datetime(event["occurred_at"]),
            }
            for event in seed["usage_events"]
        ]
    )
    database["rating_periods"].insert_many(
        [
            {
                **period,
                "_id": UUID(period["_id"]),
                "tenant_id": UUID(period["tenant_id"]),
                "period_start": _utc_datetime(period["period_start"]),
                "period_end": _utc_datetime(period["period_end"]),
                "result": {
                    **period["result"],
                    "result_id": UUID(period["result"]["result_id"]),
                    "subscription_id": UUID(period["result"]["subscription_id"]),
                    "overage_amount": Decimal128(period["result"]["overage_amount"]),
                    "created_at": _utc_datetime(period["result"]["created_at"]),
                },
            }
            for period in seed["rating_periods"]
        ]
    )
    ensure_rating_indexes()


def _utc_datetime(value: str):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)
