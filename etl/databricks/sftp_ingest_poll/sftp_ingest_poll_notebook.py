# Databricks notebook source
"""ow_tp_sftp_ingest_poll — bronze ingest registration for CUSTBILL feed files.

Medallion conversion of etl/legacy-extra/jobs/sftp_ingest_poll.ksh. Files reach
the landing volume via atomic Files API PUTs (no size-compared-twice "settle"
heuristic, no lock files, no hostname branching). This job scans the unit's
namespace slice of the landing volume and registers exactly one row per landed
file in ow_tp.bronze.custbill_raw_files, byte-transparently (files are hashed
as opaque bytes, never decoded).

Semantics (from docs/tech-partnerships/contracts/sftp_ingest_poll.json):
- empty drop            -> no-op, exit success, registry and volume untouched
- already-registered,
  byte-identical redrop -> idempotent skip, attributed in run output, no new row
- same name, different
  bytes                 -> loud failure (byte-parity conflict)
- zero-byte body or
  empty/blank file name -> loud failure, never registered
"""
import fnmatch
import hashlib
import os
import re
from dataclasses import dataclass

FILE_PATTERNS = ("CUSTBILL*.dat", "CUSTBILL*.dat.done")
NS_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
IDENT_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
VOLUME_ROOT_PATTERN = re.compile(r"^/Volumes/[A-Za-z0-9_]+/[A-Za-z0-9_]+/[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class LandedFile:
    file_name: str
    byte_count: int
    sha256: str


@dataclass(frozen=True)
class IngestPlan:
    to_insert: tuple
    duplicate_skips: tuple


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_drop(drop_dir: str) -> list:
    """Scan the landing drop for CUSTBILL files, byte-transparently.

    A missing or empty drop directory is a no-op (empty list). A zero-byte
    body or blank file name fails loudly.
    """
    if not os.path.isdir(drop_dir):
        return []
    landed = []
    for name in sorted(os.listdir(drop_dir)):
        path = os.path.join(drop_dir, name)
        if not os.path.isfile(path):
            continue
        if not any(fnmatch.fnmatch(name, pat) for pat in FILE_PATTERNS):
            continue
        if not name.strip():
            raise RuntimeError(f"refusing to register file with blank name in {drop_dir}")
        byte_count = os.path.getsize(path)
        if byte_count == 0:
            raise RuntimeError(f"refusing to register zero-byte file: {path}")
        landed.append(LandedFile(file_name=name, byte_count=byte_count, sha256=sha256_of(path)))
    return landed


def plan_ingest(existing: dict, scanned: list) -> IngestPlan:
    """Decide inserts vs idempotent duplicate skips against the registry.

    `existing` maps file_name -> sha256 for rows already registered in this
    namespace. A byte-identical redrop is skipped; a same-name file with
    different bytes is a byte-parity conflict and fails loudly.
    """
    to_insert, duplicate_skips = [], []
    for f in scanned:
        prior = existing.get(f.file_name)
        if prior is None:
            to_insert.append(f)
        elif prior == f.sha256:
            duplicate_skips.append(f)
        else:
            raise RuntimeError(
                f"byte-parity conflict for {f.file_name}: "
                f"registered sha256={prior}, landed sha256={f.sha256}"
            )
    return IngestPlan(to_insert=tuple(to_insert), duplicate_skips=tuple(duplicate_skips))


def _require(pattern: re.Pattern, value: str, what: str) -> str:
    if not pattern.fullmatch(value or ""):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def _running_in_databricks() -> bool:
    try:
        spark  # type: ignore[name-defined]  # noqa: B018
        return True
    except NameError:
        return False


def _main_databricks() -> None:
    dbutils.widgets.text("ns", "")  # type: ignore[name-defined]
    dbutils.widgets.text("volume_root", "/Volumes/ow_tp/bronze/landing")  # type: ignore[name-defined]
    dbutils.widgets.text("catalog", "ow_tp")  # type: ignore[name-defined]
    dbutils.widgets.text("schema", "bronze")  # type: ignore[name-defined]
    dbutils.widgets.text("table", "custbill_raw_files")  # type: ignore[name-defined]

    ns = _require(NS_PATTERN, dbutils.widgets.get("ns"), "ns")  # type: ignore[name-defined]
    volume_root = _require(
        VOLUME_ROOT_PATTERN,
        dbutils.widgets.get("volume_root").rstrip("/"),  # type: ignore[name-defined]
        "volume_root",
    )
    catalog = _require(IDENT_PATTERN, dbutils.widgets.get("catalog"), "catalog")  # type: ignore[name-defined]
    schema = _require(IDENT_PATTERN, dbutils.widgets.get("schema"), "schema")  # type: ignore[name-defined]
    table = _require(IDENT_PATTERN, dbutils.widgets.get("table"), "table")  # type: ignore[name-defined]

    fqtn = f"{catalog}.{schema}.{table}"
    drop_dir = f"{volume_root}/{ns}/sftp_ingest_poll"

    spark.sql(  # type: ignore[name-defined]
        f"""
        CREATE TABLE IF NOT EXISTS {fqtn} (
          ns         STRING    NOT NULL,
          file_name  STRING    NOT NULL,
          byte_count BIGINT    NOT NULL,
          sha256     STRING    NOT NULL,
          landed_at  TIMESTAMP NOT NULL
        )
        """
    )

    scanned = scan_drop(drop_dir)
    if not scanned:
        print(f"no new files in {drop_dir}; registry and volume untouched (no-op)")
        return

    existing = {
        r["file_name"]: r["sha256"]
        for r in spark.sql(  # type: ignore[name-defined]
            f"SELECT file_name, sha256 FROM {fqtn} WHERE ns = :ns", args={"ns": ns}
        ).collect()
    }
    plan = plan_ingest(existing, scanned)

    for f in plan.duplicate_skips:
        print(f"duplicate-redrop: {f.file_name} already registered byte-identically (sha256={f.sha256}); idempotent skip")

    for f in plan.to_insert:
        spark.sql(  # type: ignore[name-defined]
            f"""
            INSERT INTO {fqtn} (ns, file_name, byte_count, sha256, landed_at)
            VALUES (:ns, :file_name, :byte_count, :sha256, current_timestamp())
            """,
            args={"ns": ns, "file_name": f.file_name, "byte_count": f.byte_count, "sha256": f.sha256},
        )
        print(f"registered {f.file_name} ({f.byte_count} bytes, sha256={f.sha256})")

    print(
        f"sftp_ingest_poll done: ns={ns} inserted={len(plan.to_insert)} "
        f"duplicate_skips={len(plan.duplicate_skips)} scanned={len(scanned)}"
    )


if _running_in_databricks():
    _main_databricks()
