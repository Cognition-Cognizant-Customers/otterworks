"""Shared configuration for the `customers` MongoDB migration workload.

Connection details come from the environment so no credential is ever written
to the repo:

| Variable | Meaning | Default |
|---|---|---|
| `MONGODB_ATLAS_URI` | Atlas connection string (required) | — |
| `MONGO_DB` | target database | `ow_tp_demo` |
| `NS` | namespace stamped into `_migration.ns` | `demo` |
| `CONVERSION_BATCH_NO` | source conversion batch to migrate | `85559852` |
| `BATCH_SIZE` | rows per extract/load chunk | `1000` |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_SERVICE` | legacy Oracle estate | `localhost` / `52521` / `ow_billing` / `ow_billing` / `FREEPDB1` |
"""

import os

DEFAULT_DB = "ow_tp_demo"
DEFAULT_NS = "demo"

# NS=demo's conversion batch (the seed derives it from the namespace).
DEFAULT_BATCH_NO = 85559852

BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "1000"))

CUSTOMERS = "customers"
QUARANTINE = "customers_quarantine"

SOURCE_TABLE = "OW_BILLING.CUSTOMER_MASTER"
EAV_TABLE = "OW_BILLING.ENTITY_ATTR_VALUE"

# Indexes owned by this workload, applied by setup_collections.py.
INDEXES = {
    CUSTOMERS: [
        {"name": "tenant_status", "keys": [("tenantId", 1), ("status", 1)]},
        {"name": "customer_no_unique", "keys": [("customerNo", 1)], "unique": True},
        {"name": "signup_desc", "keys": [("dates.signup", -1)]},
        {"name": "ns", "keys": [("_migration.ns", 1)]},
    ],
    QUARANTINE: [
        {"name": "cust_field", "keys": [("custId", 1), ("field", 1)]},
        {"name": "kind_ns", "keys": [("kind", 1), ("_migration.ns", 1)]},
    ],
}


def database_name() -> str:
    return os.environ.get("MONGO_DB", DEFAULT_DB)


def namespace() -> str:
    return os.environ.get("NS", DEFAULT_NS)


def batch_no() -> int:
    return int(os.environ.get("CONVERSION_BATCH_NO", DEFAULT_BATCH_NO))


def atlas_uri() -> str:
    uri = os.environ.get("MONGODB_ATLAS_URI")
    if not uri:
        raise SystemExit("MONGODB_ATLAS_URI is not set (never hard-code it)")
    return uri


def mongo_client(**kwargs):
    from pymongo import MongoClient

    kwargs.setdefault("serverSelectionTimeoutMS", 30_000)
    kwargs.setdefault("appname", "ow-tp-mongo-customers")
    return MongoClient(atlas_uri(), **kwargs)


def oracle_dsn() -> dict:
    return {
        "user": os.environ.get("DB_USER", "ow_billing"),
        "password": os.environ.get("DB_PASSWORD", "ow_billing"),
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "52521")),
        "service_name": os.environ.get("DB_SERVICE", "FREEPDB1"),
    }
