"""Shared helpers for the tech-partnerships MongoDB migration unit.

Both the migration and the recon script read the same single environment
variable, ``MONGO_URI``, so the parent session can repoint them at Atlas
without a code change. Nothing here is Atlas-specific.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]

DOCUMENTS = "documents"
SNAPSHOTS = "document_snapshots"
QUARANTINE = "documents_quarantine"


def mongo_uri() -> str:
    fixture_uri = f"mongodb://localhost:{os.environ.get('MONGO_FIXTURE_PORT', '27017')}"
    return os.environ.get("MONGO_URI") or fixture_uri


def redacted_uri(uri: str) -> str:
    """Hide any embedded credentials so a URI can be printed or committed."""
    if "@" not in uri:
        return uri
    scheme, _, rest = uri.partition("://")
    return f"{scheme}://<redacted>@{rest.rpartition('@')[2]}"


def target_db_name(ns: str) -> str:
    return os.environ.get("MONGO_DB") or f"ow_tp_{ns}"


def validate_namespace(ns: str) -> None:
    if not legacy_common().valid_ns(ns):
        raise SystemExit(
            f"invalid namespace {ns!r}; expected only letters, digits, and underscores"
        )


def source_schema(ns: str) -> str:
    return f"otterworks_{ns}"


def legacy_common() -> ModuleType:
    """Load the immutable legacy seed helpers (checksum algorithm, PG config).

    Read-only: the recon checksums must be computed with exactly the algorithm
    that produced the baseline manifest, not a re-implementation of it.
    """
    path = ROOT / "testdata/legacy/legacy_common.py"
    spec = importlib.util.spec_from_file_location("tp_legacy_common", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(ns: str) -> dict:
    path = ROOT / f"testdata/legacy/manifests/{ns}.json"
    if not path.exists():
        raise SystemExit(
            f"baseline manifest {path} is missing; run: make seed-legacy NS={ns}"
        )
    return json.loads(path.read_text())


def mongo_client(uri: str | None = None):
    from pymongo import MongoClient

    return MongoClient(uri or mongo_uri(), tz_aware=True, appname="ow_tp_mongo_documents")


def pg_connect(ns: str):
    import psycopg2

    lc = legacy_common()
    conn = psycopg2.connect(**lc.pg_config())
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"{source_schema(ns)}.documents",))
        if cur.fetchone()[0] is None:
            conn.close()
            raise SystemExit(
                f"source schema {source_schema(ns)} is not seeded; run: make seed-legacy NS={ns}"
            )
    return conn
