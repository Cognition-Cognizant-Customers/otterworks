# Databricks notebook source
# ow_tp ingest notebook — converted replacement for etl/legacy-extra/jobs/sftp_ingest_poll.ksh
#
# Stages CUSTBILL*.dat files from the namespace drop area to incoming/ with
# atomic rename-into-place, archives a deterministic content-addressed copy,
# registers the file and its raw lines in the namespace bronze tables, and
# deletes the source from the drop area only once everything succeeded.
# Errors are never suppressed: any failure fails the run and leaves the drop
# file in place for the next poll.

import hashlib
import os
import re

dbutils.widgets.text("ns", "cnvingest")
NS = dbutils.widgets.get("ns")
if not re.fullmatch(r"[a-z0-9_]{1,24}", NS):
    raise ValueError(f"ns must match [a-z0-9_]{{1,24}}: {NS!r}")

# per-unit segment under the namespace, per the shared <ns>/<unit>/... layout rule
BASE = f"/Volumes/ow_tp/bronze/landing/{NS}/sftp_ingest_poll"
DROP = f"{BASE}/drop"
INCOMING = f"{BASE}/incoming"
ARCHIVE = f"{BASE}/archive"
STAGING = f"{BASE}/.staging"
FILES_TBL = f"ow_tp.bronze.custbill_ingest_files_{NS}"
RAW_TBL = f"ow_tp.bronze.custbill_raw_{NS}"
FILE_RE = re.compile(r"CUSTBILL[^/]*\.dat")  # same match set as the legacy ksh glob CUSTBILL*.dat

for d in (DROP, INCOMING, ARCHIVE, STAGING):
    os.makedirs(d, exist_ok=True)

spark.sql(
    f"""CREATE TABLE IF NOT EXISTS {FILES_TBL} (
        ns STRING, file_name STRING, sha256 STRING, bytes BIGINT,
        line_count BIGINT, staged_path STRING, archive_path STRING
    ) USING DELTA"""
)
spark.sql(
    f"""CREATE TABLE IF NOT EXISTS {RAW_TBL} (
        ns STRING, file_name STRING, sha256 STRING, line_no BIGINT, line STRING
    ) USING DELTA"""
)


def split_records(data: bytes) -> list:
    r"""Split opaque bytes into records on real newlines only (\n, with optional \r).

    str.splitlines() would also break on \v, \f, 0x1c-0x1e and NEL once latin-1
    decodes them, which is wrong for an opaque mainframe extract.
    """
    if not data:
        return []
    recs = data.split(b"\n")
    if recs and recs[-1] == b"":
        recs.pop()
    return [(r[:-1] if r.endswith(b"\r") else r).decode("latin-1") for r in recs]


def atomic_write(path: str, data: bytes, sha: str) -> None:
    tmp = os.path.join(STAGING, f"{os.path.basename(path)}.{sha[:16]}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.rename(tmp, path)


ingested = []
for name in sorted(os.listdir(DROP)):
    if not FILE_RE.fullmatch(name):
        continue  # legacy glob: only CUSTBILL*.dat; .filepart and others untouched
    src = os.path.join(DROP, name)
    if not os.path.isfile(src):
        continue
    stat_before = os.stat(src)
    with open(src, "rb") as fh:
        data = fh.read()
    stat_after = os.stat(src)
    if (stat_before.st_size, stat_before.st_mtime) != (stat_after.st_size, stat_after.st_mtime) or stat_after.st_size != len(data):
        print(f"skipping {name}: still being written (size/mtime changed during read)")
        continue  # leave in drop for the next poll, like the legacy settle check
    sha = hashlib.sha256(data).hexdigest()

    staged_path = os.path.join(INCOMING, name)
    archive_path = os.path.join(ARCHIVE, f"{name}.{sha[:16]}")
    stat_publish = os.stat(src)
    if (stat_publish.st_size, stat_publish.st_mtime) != (stat_after.st_size, stat_after.st_mtime):
        print(f"skipping {name}: source changed before publish; leaving in drop for the next poll")
        continue
    atomic_write(staged_path, data, sha)
    atomic_write(archive_path, data, sha)

    # byte transparency: latin-1 is a lossless 1:1 byte->codepoint mapping
    lines = split_records(data)
    # untrusted values (file names/paths) are bound as named parameters, never
    # interpolated into the SQL text; table identifiers are ns-validated above
    spark.sql(
        f"""MERGE INTO {FILES_TBL} t USING (SELECT
              :ns AS ns, :file_name AS file_name, :sha256 AS sha256,
              CAST(:bytes AS BIGINT) AS bytes, CAST(:line_count AS BIGINT) AS line_count,
              :staged_path AS staged_path, :archive_path AS archive_path
            ) s
            ON t.ns = s.ns AND t.file_name = s.file_name AND t.sha256 = s.sha256
            WHEN NOT MATCHED THEN INSERT *""",
        args={
            "ns": NS,
            "file_name": name,
            "sha256": sha,
            "bytes": len(data),
            "line_count": len(lines),
            "staged_path": staged_path,
            "archive_path": archive_path,
        },
    )
    spark.sql(
        f"DELETE FROM {RAW_TBL} WHERE ns = :ns AND file_name = :file_name AND sha256 = :sha256",
        args={"ns": NS, "file_name": name, "sha256": sha},
    )
    df = spark.createDataFrame(
        [(NS, name, sha, i + 1, line) for i, line in enumerate(lines)],
        schema="ns STRING, file_name STRING, sha256 STRING, line_no BIGINT, line STRING",
    )
    df.write.mode("append").saveAsTable(RAW_TBL)

    stat_final = os.stat(src)
    if (stat_final.st_size, stat_final.st_mtime) != (stat_after.st_size, stat_after.st_mtime):
        # roll back everything published for the torn read: staged + archive
        # copies and both bronze registrations, then leave the source in drop
        # for the next poll so no partial content stays visible anywhere
        os.remove(staged_path)
        os.remove(archive_path)
        spark.sql(
            f"DELETE FROM {FILES_TBL} WHERE ns = :ns AND file_name = :file_name AND sha256 = :sha256",
            args={"ns": NS, "file_name": name, "sha256": sha},
        )
        spark.sql(
            f"DELETE FROM {RAW_TBL} WHERE ns = :ns AND file_name = :file_name AND sha256 = :sha256",
            args={"ns": NS, "file_name": name, "sha256": sha},
        )
        print(f"rolled back {name}: source changed after read; left in drop for the next poll")
        continue
    os.remove(src)  # delete from drop only after stage+archive+registration
    ingested.append({"file_name": name, "sha256": sha, "bytes": len(data)})
    print(f"ingested {name} ({len(data)} bytes, sha256={sha})")

print(f"ow_tp_ingest_{NS}: {len(ingested)} file(s) ingested")
dbutils.notebook.exit(str(len(ingested)))
