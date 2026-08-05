"""Unit tests for pure transform functions in the storage cleanup DAG."""

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

DAG_PATH = (
    Path(__file__).resolve().parent.parent / "dags" / "otterworks_storage_cleanup.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("otterworks_storage_cleanup", DAG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


class TestKeyFormats:
    def test_quarantine_key_format(self):
        key = mod.build_quarantine_key("quarantined", "2026-08-05", "files/abc/report.pdf")
        assert key == "quarantined/2026-08-05/files/abc/report.pdf"

    def test_quarantine_prefix_constant(self):
        assert mod.QUARANTINE_PREFIX == "quarantined"

    def test_files_prefix_constant(self):
        assert mod.FILES_PREFIX == "files/"

    def test_report_key_format(self):
        assert (
            mod.build_report_key("2026-08-05")
            == "reports/storage-cleanup/2026-08-05/report.json"
        )


class TestRunDate:
    def test_scheduled_run_uses_interval_end_date(self):
        interval_end = datetime(2026, 8, 5, 2, 30, tzinfo=timezone.utc)
        assert mod.resolve_run_date(interval_end) == "2026-08-05"

    def test_manual_run_falls_back_to_utc_now(self):
        expected = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        assert mod.resolve_run_date(None) == expected


class TestTableName:
    def test_dynamodb_table_name(self):
        assert mod.DYNAMODB_TABLE_NAME == "otterworks-file-metadata"


class TestOrphanPredicate:
    def test_object_not_in_references_is_orphan(self):
        objects = [
            {"key": "files/a.txt", "size": 10, "last_modified": "2026-01-01T00:00:00"},
            {"key": "files/b.txt", "size": 20, "last_modified": "2026-01-01T00:00:00"},
        ]
        orphans = mod.find_orphans(objects, {"files/a.txt"})
        assert [o["key"] for o in orphans] == ["files/b.txt"]

    def test_all_referenced_yields_no_orphans(self):
        objects = [{"key": "files/a.txt", "size": 10, "last_modified": ""}]
        assert mod.find_orphans(objects, {"files/a.txt"}) == []

    def test_empty_references_orphans_everything(self):
        objects = [
            {"key": "files/a.txt", "size": 1, "last_modified": ""},
            {"key": "files/b.txt", "size": 2, "last_modified": ""},
        ]
        orphans = mod.find_orphans(objects, set())
        assert orphans == objects

    def test_exact_key_match_required(self):
        objects = [{"key": "files/a.txt", "size": 1, "last_modified": ""}]
        # A referenced key that is only a prefix does not protect the object
        orphans = mod.find_orphans(objects, {"files/a"})
        assert [o["key"] for o in orphans] == ["files/a.txt"]

    def test_references_as_list(self):
        objects = [{"key": "files/a.txt", "size": 1, "last_modified": ""}]
        assert mod.find_orphans(objects, ["files/a.txt"]) == []


class TestReport:
    def test_report_structure_and_math(self):
        report = mod.build_report(
            ds="2026-08-05",
            generated_at="2026-08-05T02:30:00+00:00",
            total_objects=100,
            total_size_bytes=10 * (1024 ** 3),
            orphaned_count=25,
            orphaned_bytes=2 * (1024 ** 3),
            moved_count=24,
            failed_count=1,
            quarantine_bucket="otterworks-file-quarantine",
        )
        assert report["report_type"] == "storage_cleanup"
        assert report["report_date"] == "2026-08-05"
        assert report["inventory"]["total_objects"] == 100
        assert report["inventory"]["total_size_gb"] == 10.0
        assert report["orphans"]["orphaned_objects"] == 25
        assert report["orphans"]["orphaned_size_gb"] == 2.0
        assert report["orphans"]["orphan_percentage"] == 25.0
        assert report["cleanup"]["objects_quarantined"] == 24
        assert report["cleanup"]["objects_failed"] == 1
        assert report["cleanup"]["quarantine_bucket"] == "otterworks-file-quarantine"
        assert report["savings"]["storage_freed_gb"] == 2.0
        assert report["savings"]["estimated_monthly_savings_usd"] == pytest.approx(
            round(2 * 0.023, 4)
        )

    def test_report_zero_objects_no_division_error(self):
        report = mod.build_report(
            ds="2026-08-05",
            generated_at="2026-08-05T02:30:00+00:00",
            total_objects=0,
            total_size_bytes=0,
            orphaned_count=0,
            orphaned_bytes=0,
            moved_count=0,
            failed_count=0,
            quarantine_bucket="otterworks-file-quarantine",
        )
        assert report["orphans"]["orphan_percentage"] == 0
        assert report["savings"]["estimated_monthly_savings_usd"] == 0.0
