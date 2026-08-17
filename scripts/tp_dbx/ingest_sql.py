#!/usr/bin/env python3
"""Names and SQL for the converted CUSTBILL ingest unit (dbx-ingest).

Replaces etl/legacy-extra/jobs/sftp_ingest_poll.ksh. One place for every
statement so the SQL the Databricks job runs and the SQL the recon harness runs
are provably the same text, parameterised only by namespace.

The ingest unit is byte-transparent: it never decodes, re-encodes or interprets
record content. Bronze therefore holds one row per atomically published object
(file name, byte size, content SHA-256, ingest run id), not one row per record.

Landing layout under the shared landing volume, per namespace:
  <landing>/drop/                     SFTP replacement; the off-platform sender
                                      writes complete bytes then a matching .sha256
                                      sidecar; only sidecar-bearing objects publish
  <landing>/ingest/data/<run_id>/     published objects (visible only once committed)
  <landing>/ingest/_commits/<id>.json commit marker; the single atomic publish point
"""
from __future__ import annotations

from dataclasses import dataclass

NS_PATTERN = r"[a-z0-9_]{1,24}"
IDENT_PATTERN = r"[A-Za-z0-9_]+"
RUN_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"


def require_ns(ns: str) -> str:
    if not isinstance(ns, str) or not ns or len(ns) > 24 or any(
        not (char.isascii() and (char.islower() or char.isdigit() or char == "_"))
        for char in ns
    ):
        raise SystemExit(f"namespace must match {NS_PATTERN}: {ns!r}")
    return ns


def require_ident(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or any(
        not (char.isascii() and (char.isalnum() or char == "_")) for char in value
    ):
        raise SystemExit(f"{label} must match {IDENT_PATTERN}: {value!r}")
    return value


def require_run_id(run_id: str) -> str:
    if (
        not isinstance(run_id, str)
        or not run_id
        or len(run_id) > 128
        or not run_id[0].isascii()
        or not run_id[0].isalnum()
        or any(not (char.isascii() and (char.isalnum() or char in "_-")) for char in run_id[1:])
    ):
        raise SystemExit(f"run id must match {RUN_ID_PATTERN}: {run_id!r}")
    return run_id


# Only complete drops are eligible when the matching .sha256 sidecar exists.
# These suffixes remain a defence in depth for other in-progress transfers.
IN_PROGRESS_SUFFIXES = (".part", ".tmp", ".filepart", ".inprogress")
DROP_GLOB_PREFIX = "CUSTBILL"
DROP_GLOB_SUFFIX = ".dat"


@dataclass(frozen=True)
class Names:
    catalog: str = "ow_tp"
    ns: str = "demo"

    def __post_init__(self):
        object.__setattr__(self, "catalog", require_ident(self.catalog, "catalog"))
        object.__setattr__(self, "ns", require_ns(self.ns))

    @property
    def landing(self) -> str:
        return f"/Volumes/{self.catalog}/bronze/landing/{self.ns}"

    @property
    def drop_dir(self) -> str:
        return f"{self.landing}/drop"

    @property
    def ingest_dir(self) -> str:
        return f"{self.landing}/ingest"

    @property
    def data_dir(self) -> str:
        return f"{self.ingest_dir}/data"

    @property
    def commit_dir(self) -> str:
        return f"{self.ingest_dir}/_commits"

    def run_data_dir(self, run_id: str) -> str:
        return f"{self.data_dir}/{require_run_id(run_id)}"

    def commit_path(self, run_id: str) -> str:
        return f"{self.commit_dir}/{require_run_id(run_id)}.json"

    @property
    def bronze(self) -> str:
        return f"{self.catalog}.bronze.custbill_raw_{self.ns}"

    @property
    def job(self) -> str:
        return f"ow_tp_ingest_{self.ns}"

    @property
    def notebook(self) -> str:
        return f"/Shared/ow_tp/ingest_{self.ns}"


def quote(value: str) -> str:
    """Databricks string literals honour backslash escapes, so a value ending in a
    backslash would otherwise neutralise the closing quote."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def create_bronze(n: Names) -> str:
    """Namespace-suffixed bronze table. The unit never creates or alters a shared
    object: catalog, schemas and the landing volume are parent-owned."""
    return f"""CREATE TABLE IF NOT EXISTS {n.bronze} (
              source_file STRING NOT NULL COMMENT 'CUSTBILL drop file name exactly as sent',
              byte_size BIGINT NOT NULL COMMENT 'Object size in bytes as published',
              content_sha256 STRING NOT NULL COMMENT 'SHA-256 of the published bytes',
              landed_path STRING NOT NULL COMMENT 'Volume path of the committed object',
              commit_id STRING NOT NULL COMMENT 'Commit marker that made the object visible',
              ingest_run_id STRING NOT NULL COMMENT 'Databricks job run id (or local run id) that published it',
              landed_at TIMESTAMP NOT NULL COMMENT 'Publish time on the platform')
            USING DELTA
            COMMENT 'Bronze: one row per atomically published CUSTBILL drop object; byte-transparent replacement for sftp_ingest_poll.ksh'"""


def merge_bronze(n: Names, rows: list[dict]) -> str:
    """Idempotent registration of committed objects.

    Keyed on (source_file, content_sha256): re-running over the same drop lands
    no duplicate rows. Every attribute is mandatory upstream of this statement —
    a missing file name, size, digest or run id must fail the run rather than
    fail open into a plausible-looking row (contract: null_attribution=fail).
    """
    required = ("source_file", "byte_size", "content_sha256", "landed_path", "commit_id", "ingest_run_id")
    values = []
    for row in rows:
        for key in required:
            value = row.get(key)
            if value is None or value == "":
                raise ValueError(f"refusing to register a bronze row with missing {key}: {row!r}")
        values.append(
            "({}, {}, {}, {}, {}, {})".format(
                quote(str(row["source_file"])),
                int(row["byte_size"]),
                quote(str(row["content_sha256"])),
                quote(str(row["landed_path"])),
                quote(str(row["commit_id"])),
                quote(str(row["ingest_run_id"])),
            )
        )
    if not values:
        raise ValueError("merge_bronze called with no rows; an empty drop must be a no-op instead")
    return f"""MERGE INTO {n.bronze} AS t
            USING (SELECT * FROM VALUES
              {", ".join(values)}
              AS s(source_file, byte_size, content_sha256, landed_path, commit_id, ingest_run_id)) AS s
            ON t.source_file = s.source_file AND t.content_sha256 = s.content_sha256
            WHEN NOT MATCHED THEN INSERT
              (source_file, byte_size, content_sha256, landed_path, commit_id, ingest_run_id, landed_at)
              VALUES (s.source_file, s.byte_size, s.content_sha256, s.landed_path, s.commit_id,
                      s.ingest_run_id, current_timestamp())"""


# --- recon statements: every value the report publishes is recomputed by these
# --- statements against the target tables, never carried over from a run log.

def recon_inventory(n: Names) -> str:
    return f"""SELECT source_file, byte_size, content_sha256, landed_path, ingest_run_id
            FROM {n.bronze} ORDER BY source_file"""


def recon_counts(n: Names) -> str:
    return f"""SELECT count(*) AS rows,
                   count(DISTINCT source_file) AS files,
                   count(DISTINCT content_sha256) AS digests,
                   count(DISTINCT ingest_run_id) AS runs
            FROM {n.bronze}"""


def recon_null_attribution(n: Names) -> str:
    """A landed row with a missing attribute is the fail-open case the contract
    forbids; recon asserts the target holds none."""
    return f"""SELECT count(*) AS null_attributed FROM {n.bronze}
            WHERE source_file IS NULL OR byte_size IS NULL OR content_sha256 IS NULL
               OR landed_path IS NULL OR commit_id IS NULL OR ingest_run_id IS NULL"""


def recon_duplicates(n: Names) -> str:
    return f"""SELECT count(*) AS duplicate_keys FROM (
              SELECT source_file, content_sha256, count(*) AS n FROM {n.bronze}
              GROUP BY source_file, content_sha256 HAVING count(*) > 1)"""
