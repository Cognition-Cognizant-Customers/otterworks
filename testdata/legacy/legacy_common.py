"""Shared helpers for the legacy seed-data generators and validator.

Deterministic by construction: every stream of random values is drawn from a
`random.Random` seeded from the namespace (and a per-target label), so a given
namespace reproduces byte-identical counts, checksums, and manifests across
reruns. Timestamps are derived from a fixed anchor, never from wall-clock time.
"""

import hashlib
import json
import os
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

GENERATOR_VERSION = "1"

# Fixed "as of" anchor for all generated timestamps (and the manifest's
# generated_at) so reruns are byte-identical.
ANCHOR = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)

MANIFESTS_DIR = Path(__file__).resolve().parent / "manifests"

DYNAMO_TABLE = "otterworks-file-metadata"
DATA_LAKE_BUCKET = "otterworks-data-lake"
FILES_BUCKET = "otterworks-files"

MIME_TYPES = [
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
    "video/mp4",
    "audio/mpeg",
]

EVENT_TYPES = [
    "file.uploaded",
    "file.downloaded",
    "file.trashed",
    "file.restored",
    "document.created",
    "document.updated",
    "document.shared",
    "user.login",
    "user.logout",
    "quota.warning",
]

SCALES = {
    "demo": {
        "documents": 2_000,
        "versions_min": 2,
        "versions_max": 12,
        "dynamo_items": 10_000,
        "event_days": 3,
        "users": 50,
    },
    "full": {
        "documents": 100_000,
        "versions_min": 10,
        "versions_max": 50,
        "dynamo_items": 500_000,
        "event_days": 90,
        "users": 500,
    },
}


def ns_seed(ns: str) -> int:
    return int(hashlib.sha256(ns.encode()).hexdigest()[:8], 16)


def rng_for(ns: str, label: str) -> random.Random:
    return random.Random(f"{ns_seed(ns)}:{label}")


def det_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def power_law_index(rng: random.Random, n: int, alpha: float = 1.1) -> int:
    """Zipf-like index in [0, n): a few whale users own most of the data."""
    u = rng.random()
    return min(int(n * (u ** alpha) * u), n - 1)


def anchor_minus(rng: random.Random, max_days: int) -> datetime:
    return ANCHOR - timedelta(seconds=rng.randint(0, max_days * 86400))


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NS_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def valid_ns(ns: str) -> bool:
    return bool(NS_PATTERN.fullmatch(ns))


class Checksum:
    """Order-independent, constant-memory checksum over a set of lines.

    Sums each line's md5 digest modulo 2**128, so lines can be folded in any
    order without materializing (or sorting) the whole set.
    """

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


def checksum_lines(lines: list[str]) -> str:
    ck = Checksum()
    for line in lines:
        ck.add(line)
    return ck.hexdigest()


def schema_name(ns: str) -> str:
    return f"otterworks_{ns}"


def pg_config() -> dict:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "otterworks"),
        "user": os.getenv("DB_USER", "otterworks"),
        "password": os.getenv("DB_PASSWORD", "otterworks_dev"),
    }


def aws_client(service):
    import boto3

    return boto3.client(
        service,
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def aws_resource(service):
    import boto3

    return boto3.resource(
        service,
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


# ── Manifest ──────────────────────────────────────────────────────────────────


def manifest_path(ns: str) -> Path:
    return MANIFESTS_DIR / f"{ns}.json"


def load_manifest(ns: str) -> dict:
    path = manifest_path(ns)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def merge_manifest(
    ns: str,
    targets: dict,
    anomalies: list[dict],
    owned_prefixes: tuple[str, ...],
    params: dict,
) -> dict:
    """Merge this run's targets/anomalies into the namespace manifest.

    Only entries under `owned_prefixes` (the target keys this run actually
    seeded) are replaced; entries written by other estates (e.g. oracle.*)
    are preserved untouched. `params` maps each seeded target name to the
    run parameters for that target only, so a partial re-seed never rewrites
    the recorded parameters of stores it did not touch.
    """
    manifest = load_manifest(ns)
    manifest["namespace"] = ns
    manifest["generator_version"] = manifest.get("generator_version", GENERATOR_VERSION)
    manifest["seed"] = ns_seed(ns)
    manifest["generated_at"] = iso(ANCHOR)

    merged_targets = {
        k: v
        for k, v in manifest.get("targets", {}).items()
        if not k.startswith(owned_prefixes)
    }
    merged_targets.update(targets)
    manifest["targets"] = merged_targets

    kept = [
        a
        for a in manifest.get("planted_anomalies", [])
        if not a.get("target", "").startswith(owned_prefixes)
    ]
    manifest["planted_anomalies"] = kept + sorted(
        anomalies, key=lambda a: (a["target"], a["kind"])
    )

    seed_params = manifest.get("seed_legacy_params", {})
    seed_params.update(params)
    manifest["seed_legacy_params"] = seed_params

    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path(ns).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
