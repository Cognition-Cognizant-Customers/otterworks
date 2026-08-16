"""Shared helpers for the MongoDB migration units (tech-partnerships track).

Everything here is deterministic and namespaced: database names carry the run
namespace, document ids are uuid5 of stable source keys, and the checksum
reproduces the seeder's manifest checksum (md5 over `{pk}:{amount}\n` lines
fed in sorted PK order — see testdata/legacy/oracle_billing_seed.py Checksum).
"""

import hashlib
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "testdata" / "legacy"))

import legacy_common  # noqa: E402

UUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "ow-tp-mongodb")


def det_id(*parts: str) -> str:
    """Deterministic document id: uuid5 of the stable source key parts."""
    return str(uuid.uuid5(UUID_NS, ":".join(parts)))


def target_db_name(ns: str) -> str:
    return f"ow_tp_mongodb_{ns}"


def quarantine_db_name(ns: str) -> str:
    return f"ow_tp_mongodb_{ns}_quarantine"


def oracle_batch_no(ns: str) -> int:
    """The seeder tags every row with this per-namespace batch number."""
    return legacy_common.ns_seed(ns) % 90_000_000 + 1_000_000


def ordered_pk_checksum(pairs) -> str:
    """md5 over `{pk}:{amount}\n` lines in sorted PK order.

    Matches the manifest checksums written by
    testdata/legacy/oracle_billing_seed.py (class Checksum fed in sorted
    PK order).
    """
    h = hashlib.md5()
    for pk, amount in sorted(pairs):
        h.update(f"{pk}:{amount}\n".encode())
    return h.hexdigest()


def oracle_connect():
    import oracledb

    return oracledb.connect(
        user=os.environ.get("DB_USER", "ow_billing"),
        password=os.environ.get("DB_PASSWORD", "ow_billing"),
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "52521")),
        service_name=os.environ.get("DB_SERVICE", "FREEPDB1"),
    )


def mongo_client():
    """Client for the migration target.

    Defaults to the local fixture; a live run points MONGODB_URI at Atlas
    (credential name MONGODB_ATLAS_URI — never committed, never printed).
    """
    from pymongo import MongoClient

    return MongoClient(os.environ.get("MONGODB_URI", "mongodb://localhost:27717"))


def load_manifest(ns: str) -> dict:
    return legacy_common.load_manifest(ns)
