from __future__ import annotations

import json
import re

import pytest

from scripts.tp_dbx import ingest, ingest_sql
from scripts.tp_dbx.client import SqlResult


class FakeDbx:
    def __init__(self, files=None, pairs=None):
        self.files = dict(files or {})
        self.pairs = set(pairs or [])
        self.puts = []
        self.deletes = []
        self.sql_calls = []

    def list_dir(self, path):
        prefix = path.rstrip("/") + "/"
        children = []
        for key in self.files:
            if key.startswith(prefix):
                rest = key[len(prefix):]
                if "/" not in rest:
                    children.append({"name": rest, "path": key})
        return children

    def get_file(self, path):
        return self.files[path]

    def put_file(self, path, payload):
        self.puts.append((path, payload))
        self.files[path] = payload

    def delete_file(self, path):
        self.deletes.append(path)
        self.files.pop(path, None)
        return 204

    def sql_ok(self, statement):
        self.sql_calls.append(statement)
        if statement.startswith("SELECT source_file"):
            return SqlResult("SUCCEEDED", ["source_file", "content_sha256"], [list(pair) for pair in self.pairs], "")
        return SqlResult("SUCCEEDED", [], [], "")


class Names:
    drop_dir = "/drop"
    data_dir = "/data"
    commit_dir = "/commits"

    def run_data_dir(self, run_id):
        return f"/data/{run_id}"

    def commit_path(self, run_id):
        return f"/commits/{run_id}.json"

    bronze = "ow_tp.bronze.custbill_raw_test"
    ns = "test"
    catalog = "ow_tp"


def test_eligible_selection_skips_partial_and_unrelated():
    entries = [{"name": name, "path": f"/drop/{name}"} for name in (
        "CUSTBILL_001.dat", "CUSTBILL_001.dat.sha256",
        "CUSTBILL_002.dat.part", "CUSTBILL_003.dat.tmp",
        "OTHER.dat", "CUSTBILL_004.dat.inprogress",
    )]
    assert ingest.eligible_drop_names(entries) == ["CUSTBILL_001.dat"]


def test_publish_registers_only_complete_drop_objects():
    n = Names()
    dbx = FakeDbx({"/drop/CUSTBILL_001.dat": b"complete",
                   "/drop/CUSTBILL_001.dat.sha256": ingest.sha256(b"complete").encode(),
                   "/drop/CUSTBILL_002.dat.part": b"half"})
    marker = ingest.publish(dbx, n, "run-1")
    assert [o["source_file"] for o in marker["objects"]] == ["CUSTBILL_001.dat"]
    assert json.loads(dbx.files["/commits/run-1.json"].decode()) == marker
    assert any("MERGE INTO" in sql for sql in dbx.sql_calls)


def test_publish_byte_mismatch_raises_and_does_not_commit(monkeypatch):
    n = Names()
    dbx = FakeDbx({"/drop/CUSTBILL_001.dat": b"source",
                   "/drop/CUSTBILL_001.dat.sha256": ingest.sha256(b"source").encode()})
    original = dbx.get_file

    def corrupt(path):
        data = original(path)
        return b"corrupt" if path.startswith("/data/") else data

    monkeypatch.setattr(dbx, "get_file", corrupt)
    with pytest.raises(RuntimeError, match="byte mismatch"):
        ingest.publish(dbx, n, "run-1")
    assert "/commits/run-1.json" not in dbx.files


def test_missing_completion_marker_is_skipped():
    n = Names()
    dbx = FakeDbx({"/drop/CUSTBILL_001.dat": b"incomplete"})
    assert ingest.publish(dbx, n, "run-1") is None
    assert not any(path.startswith("/data/") for path, _ in dbx.puts)
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)


def test_completion_marker_mismatch_raises_before_publish():
    n = Names()
    dbx = FakeDbx({"/drop/CUSTBILL_001.dat": b"source",
                   "/drop/CUSTBILL_001.dat.sha256": b"wrong"})
    with pytest.raises(RuntimeError, match="completion marker mismatch"):
        ingest.publish(dbx, n, "run-1")
    assert not any(path.startswith("/data/") for path, _ in dbx.puts)
    assert "/commits/run-1.json" not in dbx.files
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)


def test_empty_drop_is_noop_and_prior_rows_untouched():
    n = Names()
    dbx = FakeDbx({"/commits/old.json": b"old"}, pairs=[("old.dat", "digest")])
    assert ingest.publish(dbx, n, "run-2") is None
    assert dbx.files["/commits/old.json"] == b"old"
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)


def test_rerun_deduplicates_by_file_and_digest():
    n = Names()
    data = b"same"
    digest = ingest.sha256(data)
    dbx = FakeDbx({"/drop/CUSTBILL_001.dat": data}, pairs=[("CUSTBILL_001.dat", digest)])
    assert ingest.publish(dbx, n, "run-2") is None
    assert not any(path.startswith("/data/") for path, _ in dbx.puts)
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)


def test_merge_requires_every_attribute():
    n = ingest_sql.Names(ns="test")
    row = {"source_file": "a.dat", "byte_size": 1, "content_sha256": "x",
           "landed_path": "/x", "commit_id": "c", "ingest_run_id": "r"}
    for key in row:
        missing = dict(row)
        missing[key] = ""
        with pytest.raises(ValueError, match=f"missing {key}"):
            ingest_sql.merge_bronze(n, [missing])


def test_notebook_embeds_authoritative_sql_functions():
    n = ingest_sql.Names(ns="test")
    body = ingest.notebook_source(n)
    embedded = body.split("# BEGIN EMBEDDED INGEST_SQL\n", 1)[1].split(
        "\n# END EMBEDDED INGEST_SQL", 1
    )[0]
    namespace = {}
    exec("from dataclasses import dataclass\n" + embedded, namespace)  # noqa: S102
    embedded_names = namespace["Names"](catalog="ow_tp", ns="test")
    rows = [
        {"source_file": "CUSTBILL_001.dat", "byte_size": 4, "content_sha256": "abcd",
         "landed_path": "/data/x/CUSTBILL_001.dat", "commit_id": "x", "ingest_run_id": "x"},
        {"source_file": "CUSTBILL_002.dat", "byte_size": 5, "content_sha256": "efgh",
         "landed_path": "/data/x/CUSTBILL_002.dat", "commit_id": "x", "ingest_run_id": "x"},
    ]
    assert namespace["create_bronze"](embedded_names) == ingest_sql.create_bronze(n)
    assert namespace["merge_bronze"](embedded_names, rows) == ingest_sql.merge_bronze(n, rows)


def test_terraform_job_invariants():
    with open("infrastructure/terraform-databricks/jobs_ingest.tf", encoding="utf-8") as handle:
        text = handle.read()
    assert "max_concurrent_runs = 1" in text
    assert "enabled = true" in text
    for forbidden in ("new_cluster", "existing_cluster_id", "job_cluster", "schedule", "trigger"):
        assert not re.search(rf"^\s*{forbidden}\s*[={{]", text, re.MULTILINE)
    assert 'default = "demo"' not in text
    assert re.search(r"^\s*timeout_seconds\s*=\s*900\s*$", text, re.MULTILINE)
    assert re.search(r"^\s*project\s*=", text, re.MULTILINE)
    assert re.search(r"^\s*unit\s*=", text, re.MULTILINE)
    assert re.search(r"^\s*namespace\s*=", text, re.MULTILINE)
