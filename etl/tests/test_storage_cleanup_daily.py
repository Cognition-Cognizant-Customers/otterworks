import json
from datetime import datetime, timezone

from conftest import FakeDynamoResource, FakeDynamoTable, FakeS3Client, load_script

storage_cleanup_daily = load_script("storage_cleanup_daily")


def s3_obj(key, size):
    return {"Key": key, "Size": size, "LastModified": datetime(2026, 7, 1, tzinfo=timezone.utc)}


def run(monkeypatch, fake_config, list_pages, referenced_items, copy_fail_keys=()):
    fake_config(storage_cleanup_daily)
    s3 = FakeS3Client(list_pages=list_pages)

    if copy_fail_keys:
        original_copy = s3.copy_object

        def flaky_copy(**kwargs):
            if kwargs["CopySource"]["Key"] in copy_fail_keys:
                raise RuntimeError("copy failed")
            return original_copy(**kwargs)

        s3.copy_object = flaky_copy

    table = FakeDynamoTable([{"Items": referenced_items}])
    monkeypatch.setattr(storage_cleanup_daily.boto3, "client", lambda service, **kwargs: s3)
    monkeypatch.setattr(storage_cleanup_daily.boto3, "resource",
                        lambda service, **kwargs: FakeDynamoResource(table))
    storage_cleanup_daily.main()
    return s3


def test_orphans_quarantined_and_report_generated(monkeypatch, fake_config):
    list_pages = [
        {"Contents": [s3_obj("files/a.txt", 100), s3_obj("files/b.txt", 200)]},
        {"Contents": [s3_obj("files/orphan.bin", 1024 * 1024)]},
    ]
    referenced = [{"s3_key": "files/a.txt"}, {"s3_key": "files/b.txt"}, {"s3_key": ""}]

    s3 = run(monkeypatch, fake_config, list_pages, referenced)

    assert len(s3.copy_calls) == 1
    copy = s3.copy_calls[0]
    assert copy["CopySource"] == {"Bucket": "test-file-storage", "Key": "files/orphan.bin"}
    assert copy["Bucket"] == "test-quarantine"
    assert copy["Key"].startswith("quarantined/") and copy["Key"].endswith("/files/orphan.bin")
    assert s3.delete_calls == [{"Bucket": "test-file-storage", "Key": "files/orphan.bin"}]

    report = json.loads(s3.put_calls[-1]["Body"])
    assert report["inventory"]["total_objects"] == 3
    assert report["orphans"]["orphaned_objects"] == 1
    assert report["orphans"]["orphaned_bytes"] == 1024 * 1024
    assert report["cleanup"]["objects_quarantined"] == 1
    assert report["cleanup"]["objects_failed"] == 0


def test_no_orphans_still_generates_report(monkeypatch, fake_config):
    list_pages = [{"Contents": [s3_obj("files/a.txt", 100)]}]
    referenced = [{"s3_key": "files/a.txt"}]

    s3 = run(monkeypatch, fake_config, list_pages, referenced)

    assert s3.copy_calls == [] and s3.delete_calls == []
    report = json.loads(s3.put_calls[-1]["Body"])
    assert report["orphans"]["orphaned_objects"] == 0
    assert report["orphans"]["orphan_percentage"] == 0.0
    assert report["savings"]["estimated_monthly_savings_usd"] == 0


def test_copy_failure_counts_as_failed_and_object_not_deleted(monkeypatch, fake_config):
    list_pages = [{"Contents": [s3_obj("files/bad.bin", 500), s3_obj("files/good.bin", 500)]}]

    s3 = run(monkeypatch, fake_config, list_pages, [], copy_fail_keys={"files/bad.bin"})

    assert [c["Key"] for c in s3.delete_calls] == ["files/good.bin"]
    report = json.loads(s3.put_calls[-1]["Body"])
    assert report["cleanup"]["objects_quarantined"] == 1
    assert report["cleanup"]["objects_failed"] == 1
