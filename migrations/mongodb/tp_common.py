"""Shared helpers for the MongoDB migration units.

Reuses the seeders' namespace-seed derivation (testdata/legacy/legacy_common.py)
so source scoping (batch_no) matches what the deterministic seeders wrote.
"""

import hashlib
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "testdata" / "legacy"))

import legacy_common  # noqa: E402

MANIFESTS_DIR = legacy_common.MANIFESTS_DIR


def ns_seed(ns: str) -> int:
    return legacy_common.ns_seed(ns)


def batch_no(ns: str) -> int:
    """The namespace batch tag the Oracle seeder stamps on every row."""
    return ns_seed(ns) % 90_000_000 + 1_000_000


UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "otterworks://tp/mongo_invoices")


def det_id(ns: str, kind: str, source_id: str) -> str:
    """Deterministic target id: uuid5 over unit/namespace/kind/source id."""
    return str(uuid.uuid5(UUID_NAMESPACE, f"{ns}:{kind}:{source_id}"))


def valid_ns(ns: str) -> bool:
    return legacy_common.valid_ns(ns)


def target_db_name(ns: str) -> str:
    return f"ow_tp_mongodb_{ns}"


def quarantine_db_name(ns: str) -> str:
    return f"ow_tp_mongodb_{ns}_quarantine"


def mongo_uri(run_mode: str = "fixture") -> str:
    """fixture runs never touch the shared Atlas cluster: MONGODB_ATLAS_URI is
    only consulted when run_mode is explicitly 'live' (the parent's window)."""
    if run_mode == "live":
        uri = os.getenv("MONGODB_ATLAS_URI")
        if not uri:
            raise SystemExit("run_mode=live requires MONGODB_ATLAS_URI")
        return uri
    return os.getenv("MONGODB_URI") or "mongodb://localhost:27777/?directConnection=true"


class OrderedChecksum:
    """md5 of ordered PK+amount lines, matching the seeders' manifest checksum
    (rows must be fed in sorted-PK order; line format is '<pk>:<amount>\\n')."""

    def __init__(self) -> None:
        self._h = hashlib.md5()
        self.count = 0

    def add(self, pk: str, amount: str) -> None:
        self._h.update(f"{pk}:{amount}\n".encode())
        self.count += 1

    def hexdigest(self) -> str:
        return self._h.hexdigest()


def amount_str(value) -> str:
    """Render a NUMBER(14,2) amount exactly as the seeder's checksum did."""
    if value is None:
        return "None"
    return f"{value:.2f}"

