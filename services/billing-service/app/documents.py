from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database

from app.config import settings
from db.mongo_seed import seed

_client: MongoClient | None = None


def configured() -> bool:
    return bool(settings.mongodb_uri)


def client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.mongodb_uri, uuidRepresentation="standard")
    return _client


def database() -> Database:
    return client()[settings.mongodb_db]


def reset_documents() -> None:
    seed(database(), settings.mongodb_ns)
