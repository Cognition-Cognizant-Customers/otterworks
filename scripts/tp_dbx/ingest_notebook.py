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

BASE = f"/Volumes/ow_tp/bronze/landing/{NS}"
DROP = f"{BASE}/drop"
INCOMING = f"{BASE}/incoming"
ARCHIVE = f"{BASE}/archive"
STAGING = f"{BASE}/.staging"
FILES_TBL = f"ow_tp.bronze.custbill_ingest_files_{NS}"
RAW_TBL = f"ow_tp.bronze.custbill_raw_{NS}"
FILE_RE = re.compile(r"CUSTBILL[A-Za-z0-9_]*\.dat")

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
    with open(src, "rb") as fh:
        data = fh.read()
    sha = hashlib.sha256(data).hexdigest()

    staged_path = os.path.join(INCOMING, name)
    archive_path = os.path.join(ARCHIVE, f"{name}.{sha[:16]}")
    atomic_write(staged_path, data, sha)
    atomic_write(archive_path, data, sha)

    # byte transparency: latin-1 is a lossless 1:1 byte->codepoint mapping
    lines = data.decode("latin-1").splitlines()
    spark.sql(
        f"""MERGE INTO {FILES_TBL} t USING (SELECT
              '{NS}' AS ns, '{name}' AS file_name, '{sha}' AS sha256,
              {len(data)}L AS bytes, {len(lines)}L AS line_count,
              '{staged_path}' AS staged_path, '{archive_path}' AS archive_path
            ) s
            ON t.ns = s.ns AND t.file_name = s.file_name AND t.sha256 = s.sha256
            WHEN NOT MATCHED THEN INSERT *"""
    )
    spark.sql(
        f"DELETE FROM {RAW_TBL} WHERE ns = '{NS}' AND file_name = '{name}' AND sha256 = '{sha}'"
    )
    df = spark.createDataFrame(
        [(NS, name, sha, i + 1, line) for i, line in enumerate(lines)],
        schema="ns STRING, file_name STRING, sha256 STRING, line_no BIGINT, line STRING",
    )
    df.write.mode("append").saveAsTable(RAW_TBL)

    os.remove(src)  # delete from drop only after stage+archive+registration
    ingested.append({"file_name": name, "sha256": sha, "bytes": len(data)})
    print(f"ingested {name} ({len(data)} bytes, sha256={sha})")

print(f"ow_tp_ingest_{NS}: {len(ingested)} file(s) ingested")
dbutils.notebook.exit(str(len(ingested)))
