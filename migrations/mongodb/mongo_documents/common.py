"""Shared helpers for the mongo_documents migration unit.

Deterministic by construction: target document ids derive from uuid5 over the
(unit, namespace, source id) triple, no wall-clock values are embedded in
migrated documents, and checksums use the same order-independent md5 fold as
the legacy seed manifests (testdata/legacy/legacy_common.py Checksum).
"""

from __future__ import annotations

import hashlib
import os
import uuid

UNIT = "mongo_documents"
UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "otterworks://tp/mongo_documents")

BATCH_SIZE = 500  # per-batch trigger granularity per the unit contract


def det_id(ns: str, kind: str, source_id: str) -> str:
    """Deterministic target id: uuid5 over unit/namespace/kind/source id."""
    return str(uuid.uuid5(UUID_NAMESPACE, f"{ns}:{kind}:{source_id}"))


def target_db_name(ns: str) -> str:
    return f"ow_tp_mongodb_{ns}"


def quarantine_db_name(ns: str) -> str:
    return f"ow_tp_mongodb_{ns}_quarantine"


def pg_schema(ns: str) -> str:
    return f"otterworks_{ns}"


def pg_config() -> dict:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "otterworks"),
        "user": os.getenv("DB_USER", "otterworks"),
        "password": os.getenv("DB_PASSWORD", "otterworks_dev"),
    }


def mongo_uri() -> str:
    """Connection string for the target MongoDB.

    Defaults to the local fixture. For the live window the parent exports
    MONGODB_URI from the MONGODB_ATLAS_URI secret; values are never printed.
    """
    return os.getenv("MONGODB_URI", "mongodb://localhost:27027")


class Checksum:
    """Order-independent md5 fold, identical to the legacy seed generators."""

    _MOD = 1 << 128

    def __init__(self) -> None:
        self._total = 0
        self.count = 0

    def add(self, line: str) -> None:
        digest = hashlib.md5(line.encode()).digest()
        self._total = (self._total + int.from_bytes(digest, "big")) % self._MOD
        self.count += 1

    def hexdigest(self) -> str:
        return f"{self._total:032x}"


def contiguous_gaps(version_numbers: list[int], expected_max: int) -> list[int]:
    """Missing version numbers in 1..expected_max.

    expected_max is the document's `version` column (the head version number),
    so a gap at the head — invisible to pure contiguity over the embedded
    subarray — is still detected.
    """
    present = set(version_numbers)
    return [v for v in range(1, max(expected_max, *present, 0) + 1)
            if v not in present]
