from __future__ import annotations

from pathlib import Path

import handler_parse
import pytest


FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = (FIXTURES / "CUSTBILL_SAMPLE_001.dat").read_bytes()
EXPECTED = (FIXTURES / "CUSTBILL_SAMPLE_001.psv").read_bytes()


class FakeBody:
    def __init__(self, value: bytes):
        self.value = value

    def read(self) -> bytes:
        return self.value


class FakeS3:
    def __init__(self, value: bytes):
        self.value = value
        self.puts: list[tuple[str, str, bytes]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, FakeBody]:
        return {"Body": FakeBody(self.value)}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.puts.append((Bucket, Key, Body))


class FakeBatchWriter:
    def __init__(self, table: "FakeTable"):
        self.table = table

    def __enter__(self) -> "FakeBatchWriter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def put_item(self, *, Item: dict[str, str]) -> None:
        self.table.items.append(Item)


class FakeTable:
    def __init__(self):
        self.items: list[dict[str, str]] = []

    def batch_writer(self) -> FakeBatchWriter:
        return FakeBatchWriter(self)


def setup_handler(monkeypatch, source: bytes = SOURCE, **event):
    s3 = FakeS3(source)
    table = FakeTable()
    monkeypatch.setattr(handler_parse, "s3", s3)
    monkeypatch.setattr(handler_parse, "billing_table", table)
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.setenv("TABLE_NAME", "test-table")
    payload = {
        "ns": "demo",
        "bucket": "test-bucket",
        "key": "landing/demo/CUSTBILL_SAMPLE_001.dat",
        "filename": "CUSTBILL_SAMPLE_001.dat",
        **event,
    }
    return payload, s3, table


def invoke(monkeypatch, source: bytes = SOURCE, **event):
    payload, s3, table = setup_handler(monkeypatch, source, **event)
    return handler_parse.handler(
        payload,
        None,
    ), s3, table


@pytest.mark.parametrize("filename", ["../../evil.psv", "foo/bar.dat"])
def test_handler_rejects_filename_path_traversal_before_writes(
    monkeypatch, filename: str
) -> None:
    payload, s3, table = setup_handler(monkeypatch, filename=filename)

    with pytest.raises(ValueError):
        handler_parse.handler(payload, None)

    assert s3.puts == []
    assert table.items == []


def test_handler_rejects_namespace_path_traversal_before_writes(monkeypatch) -> None:
    payload, s3, table = setup_handler(monkeypatch, ns="../demo")

    with pytest.raises(ValueError):
        handler_parse.handler(payload, None)

    assert s3.puts == []
    assert table.items == []


def test_handler_uses_normal_namespace_and_filename_segments_unchanged(
    monkeypatch,
) -> None:
    result, s3, table = invoke(monkeypatch)

    assert result["parsed_key"] == "parsed/demo/CUSTBILL_SAMPLE_001.psv"
    assert s3.puts[0][1] == "parsed/demo/CUSTBILL_SAMPLE_001.psv"
    assert len(table.items) == 5


def test_handler_writes_golden_and_one_idempotent_item_per_record(monkeypatch) -> None:
    result, s3, table = invoke(monkeypatch)

    assert s3.puts == [("test-bucket", "parsed/demo/CUSTBILL_SAMPLE_001.psv", EXPECTED)]
    assert result == {
        "ns": "demo",
        "parsed_key": "parsed/demo/CUSTBILL_SAMPLE_001.psv",
        "records": 5,
        "trailer_count": 5,
        "trailer_match": True,
    }
    assert len(table.items) == 5
    assert table.items[0] == {
        "ns": "demo",
        "rec": "CUSTBILL_SAMPLE_001.dat#000001",
        "cust_id": "C000699637",
        "cust_name": "INITECH SA",
        "bill_date": "2025-03-23",
        "amount": "4393.35",
        "currency": "USD",
        "rec_type": "01",
        "source_key": "landing/demo/CUSTBILL_SAMPLE_001.dat",
    }


def test_handler_reports_trailer_mismatch_without_changing_bytes(monkeypatch) -> None:
    source = SOURCE.replace(b"TRL0000000005", b"TRL0000000006")

    result, s3, _ = invoke(monkeypatch, source)

    assert result["trailer_match"] is False
    assert s3.puts[0][2] == EXPECTED


def test_handler_builds_items_from_fixed_width_fields_with_embedded_pipe(monkeypatch) -> None:
    source = (
        b"C000000001"
        + b"NAME|PIPE" + b" " * 21
        + b"20250102"
        + b"000000000100"
        + b"USD01\n"
        + b"TRL0000000001" + b" " * 52 + b"\n"
    )

    _, s3, table = invoke(monkeypatch, source)

    assert table.items[0]["cust_name"] == "NAME|PIPE"
    assert s3.puts[0][2] == (
        b"C000000001|NAME|PIPE-  -  |202501.02|000000000100|USD|01\n"
    )


def test_handler_accepts_zero_record_trailer(monkeypatch) -> None:
    source = b"TRL0000000000" + b" " * 52 + b"\n"

    result, s3, table = invoke(monkeypatch, source)

    assert result["records"] == 0
    assert result["trailer_count"] == 0
    assert result["trailer_match"] is True
    assert s3.puts[0][2] == b""
    assert table.items == []


def test_handler_derives_missing_namespace_and_passes_malformed_record(monkeypatch) -> None:
    malformed = b"BAD\n" + SOURCE

    result, _, table = invoke(
        monkeypatch,
        malformed,
        ns=None,
        key="landing/demo/CUSTBILL_SAMPLE_001.dat",
    )

    assert result["ns"] == "demo"
    assert result["records"] == 6
    assert table.items[0]["cust_id"] == "BAD"
    assert table.items[0]["amount"] == "0.00"
