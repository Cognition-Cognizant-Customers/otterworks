"""Atlas connection helpers and the target shape for the `invoices` workload.

The target database is per-namespace (`ow_tp_<ns>`) so concurrent demo runs
never collide; credentials come from the `MONGODB_ATLAS_URI` environment
secret and are never written to disk or logged.
"""

import os
import re

import pymongo

INVOICES = "invoices"
ORPHANED_LINES = "invoice_lines_orphaned"

NS_PATTERN = re.compile(r"[A-Za-z0-9_]+")

# Index specs are declared once and applied by setup_collections.py; keyed by
# collection so the setup script stays a dumb, idempotent applier.
INDEXES = {
    INVOICES: [
        pymongo.IndexModel([("customerId", 1), ("invoiceDate", -1)],
                           name="customerId_1_invoiceDate_-1"),
        pymongo.IndexModel([("invoiceNo", 1)], name="invoiceNo_1", unique=True),
        pymongo.IndexModel([("tenantId", 1), ("status", 1)],
                           name="tenantId_1_status_1"),
        pymongo.IndexModel([("lines.lineId", 1)], name="lines.lineId_1"),
    ],
    ORPHANED_LINES: [
        # Orphans are read by the dangling pointer they carry and swept by
        # quarantine reason.
        pymongo.IndexModel([("raw.INVOICE_ID", 1)], name="raw.INVOICE_ID_1"),
        pymongo.IndexModel([("quarantine_reason", 1)], name="quarantine_reason_1"),
    ],
}


def valid_ns(ns: str) -> bool:
    return bool(NS_PATTERN.fullmatch(ns))


def database_name(ns: str) -> str:
    return f"ow_tp_{ns}"


def client() -> pymongo.MongoClient:
    uri = os.environ.get("MONGODB_ATLAS_URI")
    if not uri:
        raise SystemExit("MONGODB_ATLAS_URI is not set")
    return pymongo.MongoClient(uri, serverSelectionTimeoutMS=30_000)


def database(ns: str):
    if not valid_ns(ns):
        raise SystemExit("NS must match ^[A-Za-z0-9_]+$")
    return client()[database_name(ns)]
