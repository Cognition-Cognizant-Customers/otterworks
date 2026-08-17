from __future__ import annotations

import json
import re
from types import SimpleNamespace

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
        children = {}
        for key in self.files:
            if key.startswith(prefix):
                rest = key[len(prefix):]
                name = rest.split("/", 1)[0]
                child_path = f"{prefix}{name}".rstrip("/")
                children[child_path] = {
                    "name": name,
                    "path": child_path,
                    **({"is_directory": True} if "/" in rest else {}),
                }
        return list(children.values())

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


@pytest.mark.parametrize(
    ("catalog", "ns", "label"),
    [("ow/tp", "test", "catalog"), ("ow_tp", "../otherns", "namespace")],
)
def test_names_validate_namespace_and_catalog(catalog, ns, label):
    with pytest.raises(SystemExit, match=f"{label} must match"):
        ingest_sql.Names(catalog=catalog, ns=ns)


class OrderingDbx:
    def __init__(self, files=None, delete_status=None):
        self.files = dict(files or {})
        self.events = []
        self.delete_status = delete_status

    def put_file(self, path, payload):
        self.events.append(("put", path))
        self.files[path] = payload

    def delete_file(self, path):
        self.events.append(("delete", path))
        if self.delete_status is not None:
            return self.delete_status
        if path not in self.files:
            return 404
        del self.files[path]
        return 204


def test_send_drop_retracts_sidecar_before_overwriting_data(tmp_path):
    source = tmp_path / "CUSTBILL_001.dat"
    source.write_bytes(b"new-bytes")
    drop_dir = ingest_sql.Names(catalog="ow_tp", ns="test").drop_dir
    target = f"{drop_dir}/CUSTBILL_001.dat"
    dbx = OrderingDbx({f"{target}.sha256": b"old-digest"})
    args = SimpleNamespace(catalog="ow_tp", ns="test", source=str(tmp_path), strip_suffix="")

    assert ingest.cmd_send_drop(dbx, args) == 0
    assert dbx.events.index(("delete", f"{target}.sha256")) < dbx.events.index(("put", target))
    assert dbx.files[f"{target}.sha256"] == ingest.sha256(b"new-bytes").encode()


def test_send_drop_tolerates_missing_sidecar(tmp_path):
    source = tmp_path / "CUSTBILL_001.dat"
    source.write_bytes(b"first-send")
    dbx = OrderingDbx()
    args = SimpleNamespace(catalog="ow_tp", ns="test", source=str(tmp_path), strip_suffix="")

    assert ingest.cmd_send_drop(dbx, args) == 0
    drop_dir = ingest_sql.Names(catalog="ow_tp", ns="test").drop_dir
    assert ("delete", f"{drop_dir}/CUSTBILL_001.dat.sha256") in dbx.events
    assert dbx.files[f"{drop_dir}/CUSTBILL_001.dat"] == b"first-send"


def test_send_drop_rejects_sidecar_retraction_failure(tmp_path):
    source = tmp_path / "CUSTBILL_001.dat"
    source.write_bytes(b"new-bytes")
    dbx = OrderingDbx(delete_status=500)
    args = SimpleNamespace(catalog="ow_tp", ns="test", source=str(tmp_path), strip_suffix="")

    with pytest.raises(ingest.DbxError, match="refusing to overwrite data"):
        ingest.cmd_send_drop(dbx, args)
    assert not any(event[0] == "put" for event in dbx.events)


def test_send_drop_reports_mixed_source_suffixes(tmp_path, capsys):
    (tmp_path / "CUSTBILL_A.dat").write_bytes(b"matching")
    (tmp_path / "CUSTBILL_B.dat.done").write_bytes(b"skipped")
    dbx = OrderingDbx()
    args = SimpleNamespace(catalog="ow_tp", ns="test", source=str(tmp_path), strip_suffix="")

    assert ingest.cmd_send_drop(dbx, args) == 0
    output = capsys.readouterr().out
    assert "CUSTBILL_B.dat.done" in output
    assert "not matching configured suffix" in output
    assert any(event[0] == "put" and event[1].endswith("/CUSTBILL_A.dat") for event in dbx.events)
    assert not any(event[1].endswith("/CUSTBILL_B.dat") for event in dbx.events)


def test_send_drop_rejects_directory_with_no_matching_files(tmp_path):
    (tmp_path / "CUSTBILL_B.dat.done").write_bytes(b"skipped")
    dbx = OrderingDbx()
    args = SimpleNamespace(catalog="ow_tp", ns="test", source=str(tmp_path), strip_suffix="")

    with pytest.raises(SystemExit, match="no CUSTBILL source files matched"):
        ingest.cmd_send_drop(dbx, args)
    assert dbx.events == []


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


def test_missing_completion_marker_is_skipped(capsys):
    n = Names()
    dbx = FakeDbx({"/drop/CUSTBILL_001.dat": b"incomplete"})
    assert ingest.publish(dbx, n, "run-1") is None
    assert not any(path.startswith("/data/") for path, _ in dbx.puts)
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)
    output = capsys.readouterr().out
    assert "CUSTBILL_001.dat" in output
    assert "await completion sidecars" in output


def test_completion_marker_mismatch_raises_before_publish():
    n = Names()
    dbx = FakeDbx({"/drop/CUSTBILL_001.dat": b"source",
                   "/drop/CUSTBILL_001.dat.sha256": b"wrong"})
    with pytest.raises(RuntimeError, match="completion marker mismatch"):
        ingest.publish(dbx, n, "run-1")
    assert not any(path.startswith("/data/") for path, _ in dbx.puts)
    assert "/commits/run-1.json" not in dbx.files
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)


@pytest.mark.parametrize("run_id", ["../otherns", "..", "run/id"])
def test_publish_rejects_unsafe_run_id_before_side_effects(run_id):
    n = Names()
    data = b"source"
    dbx = FakeDbx({
        "/drop/CUSTBILL_001.dat": data,
        "/drop/CUSTBILL_001.dat.sha256": ingest.sha256(data).encode(),
    })
    with pytest.raises(SystemExit, match="run id must match"):
        ingest.publish(dbx, n, run_id)
    assert dbx.puts == []
    assert dbx.sql_calls == []


@pytest.mark.parametrize("run_id", ["12345", "local-" + "a" * 32])
def test_publish_accepts_valid_run_ids(run_id):
    n = Names()
    data = b"source"
    dbx = FakeDbx({
        "/drop/CUSTBILL_001.dat": data,
        "/drop/CUSTBILL_001.dat.sha256": ingest.sha256(data).encode(),
    })
    marker = ingest.publish(dbx, n, run_id)
    assert marker["run_id"] == run_id
    assert f"/data/{run_id}/CUSTBILL_001.dat" in dbx.files
    assert f"/commits/{run_id}.json" in dbx.files


def test_empty_drop_is_noop_and_prior_rows_untouched():
    n = Names()
    dbx = FakeDbx({"/commits/old.json": b"old"}, pairs=[("old.dat", "digest")])
    assert ingest.publish(dbx, n, "run-2") is None
    assert dbx.files["/commits/old.json"] == b"old"
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)


def test_status_and_recon_classify_committed_and_uncommitted_objects(capsys):
    n = ingest_sql.Names(catalog="ow_tp", ns="test")
    committed_path = f"{n.run_data_dir('run-1')}/CUSTBILL_001.dat"
    orphan_path = f"{n.run_data_dir('run-2')}/CUSTBILL_002.dat"
    marker = {
        "run_id": "run-1",
        "objects": [{"landed_path": committed_path}],
    }
    dbx = FakeDbx({
        committed_path: b"committed",
        orphan_path: b"orphan",
        n.commit_path("run-1"): json.dumps(marker).encode(),
    })
    args = SimpleNamespace(catalog="ow_tp", ns="test", out="")

    assert ingest.cmd_status(dbx, args) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["object_counts"] == {"committed": 1, "total": 2, "uncommitted": 1}
    assert {item["path"]: item["status"] for item in status["object_status"]} == {
        committed_path: "committed",
        orphan_path: "uncommitted",
    }

    assert ingest.cmd_recon_collect(dbx, args) == 0
    recon = json.loads(capsys.readouterr().out)
    assert {item["path"]: item["status"] for item in recon["volume"]["objects"]} == {
        committed_path: "committed",
        orphan_path: "uncommitted",
    }
    assert recon["volume"]["object_counts"] == {"committed": 1, "total": 2, "uncommitted": 1}


def test_rerun_deduplicates_by_file_and_digest():
    n = Names()
    data = b"same"
    digest = ingest.sha256(data)
    dbx = FakeDbx({"/drop/CUSTBILL_001.dat": data}, pairs=[("CUSTBILL_001.dat", digest)])
    assert ingest.publish(dbx, n, "run-2") is None
    assert not any(path.startswith("/data/") for path, _ in dbx.puts)
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)


def test_publish_rejects_reused_run_id_before_landing():
    n = Names()
    data = b"new"
    dbx = FakeDbx({
        "/drop/CUSTBILL_001.dat": data,
        "/drop/CUSTBILL_001.dat.sha256": ingest.sha256(data).encode(),
        "/commits/run-1.json": b'{"run_id":"run-1","objects":[]}',
    })

    with pytest.raises(RuntimeError, match="use a fresh run id"):
        ingest.publish(dbx, n, "run-1")
    assert not any(path.startswith("/data/") for path, _ in dbx.puts)
    assert not any("MERGE INTO" in sql for sql in dbx.sql_calls)


def test_publish_repairs_existing_matching_marker_without_reupload():
    n = ingest_sql.Names(catalog="ow_tp", ns="test")
    payload = b"repair-me"
    digest = ingest.sha256(payload)
    landed_path = n.run_data_dir("run-1") + "/CUSTBILL_001.dat"
    marker = {
        "run_id": "run-1",
        "committed_at": "2026-08-17T04:00:00Z",
        "objects": [{
            "source_file": "CUSTBILL_001.dat",
            "byte_size": len(payload),
            "content_sha256": digest,
            "landed_path": landed_path,
        }],
    }
    marker_bytes = json.dumps(marker, sort_keys=True).encode()
    dbx = FakeDbx({
        n.drop_dir + "/CUSTBILL_001.dat": payload,
        n.drop_dir + "/CUSTBILL_001.dat.sha256": digest.encode(),
        landed_path: payload,
        n.commit_path("run-1"): marker_bytes,
    })

    result = ingest.publish(dbx, n, "run-1")

    assert result == marker
    assert dbx.puts == []
    assert dbx.files[n.commit_path("run-1")] == marker_bytes
    assert any("MERGE INTO" in sql for sql in dbx.sql_calls)


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
    assert "publish-deferred" in body
    assert "ineligible_names" in body
    assert "commit marker already exists for run id" in body
    embedded = body.split("# BEGIN EMBEDDED INGEST_SQL\n", 1)[1].split(
        "\n# END EMBEDDED INGEST_SQL", 1
    )[0]
    namespace = {}
    exec("from dataclasses import dataclass\n" + embedded, namespace)  # noqa: S102
    embedded_names = namespace["Names"](catalog="ow_tp", ns="test")
    with pytest.raises(SystemExit, match="namespace must match"):
        namespace["Names"](catalog="ow_tp", ns="../otherns")
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
    assert 'default     = "/Shared/ow_tp/ingest_cnvingest"' not in text
    assert 'notebook_path = local.ow_tp_ingest_notebook_path' in text
    assert '"/Shared/ow_tp/ingest_${var.ow_tp_ingest_ns}"' in text
    assert 'source  = "databricks/databricks"' in text
