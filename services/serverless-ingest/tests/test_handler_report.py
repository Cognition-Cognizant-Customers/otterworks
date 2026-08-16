"""Byte-parity tests for the finance report handler.

The fixtures under `fixtures/` are real parsed PSV slices; the expected CSV was
produced by running the legacy `etl/legacy-extra/jobs/finance_excel_report.pl`
over exactly those two files, so these tests pin the legacy bytes (header,
`sort keys` row order, `%.2f` totals, `UNKNOWN(<rt>)` fallback) rather than a
re-derivation of them.
"""

import os
from pathlib import Path

import pytest

import handler_report

FIXTURES = Path(__file__).parent / "fixtures"
PSV_FILES = ["CUSTBILL_FIX_001.psv", "CUSTBILL_FIX_002.psv"]
EXPECTED_CSV = (FIXTURES / "expected_finance_billing.csv").read_bytes()


class FakeS3:
    """Minimal in-memory stand-in for the S3 client (no network)."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.metadata = {}
        self.puts = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class Paginator:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for k in outer.objects if k.startswith(Prefix))
                # exercise the pagination path
                for i in range(0, max(len(keys), 1), 1):
                    page = keys[i : i + 1]
                    yield {"Contents": [{"Key": k} for k in page]} if page else {}

        return Paginator()

    def get_object(self, Bucket, Key):
        class Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {"Body": Body(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, Metadata=None, **kwargs):
        self.objects[Key] = Body
        self.metadata[Key] = dict(Metadata or {})
        self.puts.append((Bucket, Key, Body))
        return {}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Metadata": self.metadata.get(Key, {})}


@pytest.fixture(autouse=True)
def lambda_env(monkeypatch):
    monkeypatch.setenv("BUCKET", "ow-tp-ingest-test")
    monkeypatch.setenv("TABLE_NAME", "ow-tp-billing-records")
    monkeypatch.setenv("TZ", "UTC")


@pytest.fixture
def fake_s3(monkeypatch):
    objects = {
        f"parsed/demo/{name}": (FIXTURES / name).read_bytes() for name in PSV_FILES
    }
    client = FakeS3(objects)
    monkeypatch.setattr(handler_report, "_s3", lambda: client)
    return client


def _psv_lines():
    lines = []
    for name in PSV_FILES:
        lines.extend((FIXTURES / name).read_text().splitlines())
    return lines


def test_render_csv_is_byte_identical_to_legacy_report():
    csv = handler_report.render_csv(handler_report.aggregate(_psv_lines()))
    assert csv.encode() == EXPECTED_CSV


def test_aggregate_row_order_and_record_type_labels():
    rows = handler_report.aggregate(_psv_lines())
    assert [(r["currency"], r["record_type"]) for r in rows] == [
        ("EUR", "INVOICE"),
        ("EUR", "CREDIT"),
        ("GBP", "INVOICE"),
        ("GBP", "UNKNOWN(07)"),
        ("USD", "INVOICE"),
        ("USD", "CREDIT"),
    ]
    gbp_unknown = rows[3]
    assert gbp_unknown["count"] == 2
    assert gbp_unknown["total"] == pytest.approx(1235.55)


def test_aggregate_skips_records_with_an_empty_customer():
    # legacy `next if ($cust eq "")` — an empty line splits to one empty field
    lines = ["", "|OTTER|2025-01-01|1.00|USD|01"] + _psv_lines()
    assert handler_report.aggregate(lines) == handler_report.aggregate(_psv_lines())


def test_aggregate_tolerates_malformed_records_like_the_legacy_perl():
    # expectations produced by running etl/legacy-extra/jobs/finance_excel_report.pl
    # over exactly these records:
    #   Currency,RecordType,RecordCount,TotalAmount
    #   34.00,UNKNOWN(USD),1,2025.00
    #   GBP,CREDIT,1,5.50
    #   USD,INVOICE,1,0.00
    #   ,UNKNOWN(),2,12.00
    rows = handler_report.aggregate(
        [
            "C000000001|OTTER LTD|2025-01-01|12.00",  # short: ccy/rt undef
            "C000000002|PIPE|NAME|2025-01-02|34.00|USD|01",  # embedded pipe: fields shift
            "C000000003|X|2025-01-03|NOTANUMBER|USD|01",  # non-numeric amount -> 0
            "C000000004|X|2025-01-04|5.5abc|GBP|02",  # numeric prefix wins
            "   ",  # non-empty cust, everything else undef
        ]
    )
    assert [
        (r["currency"], r["record_type"], r["count"], round(r["total"], 2)) for r in rows
    ] == [
        ("34.00", "UNKNOWN(USD)", 1, 2025.00),
        ("GBP", "CREDIT", 1, 5.50),
        ("USD", "INVOICE", 1, 0.00),
        ("", "UNKNOWN()", 2, 12.00),
    ]


def test_handler_reads_latin1_psv_bytes(monkeypatch):
    # the parser writes latin-1 bytes (non-UTF-8 names round-trip), so decoding as
    # UTF-8 here would fail the whole namespace's report
    client = FakeS3({"parsed/x/CUSTBILL_X_001.psv": b"C1|OTTER\xa0LTD|2025-01-01|1.00|USD|01\n"})
    monkeypatch.setattr(handler_report, "_s3", lambda: client)

    result = handler_report.handler({"ns": "x"}, None)

    assert client.objects[result["report_key"]].endswith(b"USD,INVOICE,1,1.00\n")


def test_handler_splits_records_on_newlines_only(monkeypatch):
    # the parser keeps \v/\f/\x85 inside the name field and the legacy Perl reader
    # only ever broke on "\n", so such a record must stay one record
    client = FakeS3(
        {"parsed/y/CUSTBILL_Y_001.psv": b"C1|NAME\x0bWITH|2025-01-01|1.00|USD|01\n"}
    )
    monkeypatch.setattr(handler_report, "_s3", lambda: client)

    result = handler_report.handler({"ns": "y"}, None)

    assert result["rows"] == 1
    assert client.objects[result["report_key"]] == (
        b"Currency,RecordType,RecordCount,TotalAmount\nUSD,INVOICE,1,1.00\n"
    )


def test_empty_parsed_prefix_writes_header_only_report(monkeypatch):
    client = FakeS3({})
    monkeypatch.setattr(handler_report, "_s3", lambda: client)

    result = handler_report.handler({"ns": "empty"}, None)

    assert result["rows"] == 0
    assert result["files_aggregated"] == 0
    assert client.objects[result["report_key"]] == b"Currency,RecordType,RecordCount,TotalAmount\n"


def test_handler_writes_report_and_identical_xls_copy(fake_s3):
    result = handler_report.handler({"ns": "demo", "parse": {"records": 10}}, None)

    stamp = handler_report.report_stamp()
    assert result == {
        "ns": "demo",
        "report_key": f"reports/demo/finance_billing_{stamp}.csv",
        "xls_key": f"reports/demo/finance_billing_{stamp}.xls",
        "rows": 6,
        "files_aggregated": 2,
        "published": True,
    }
    assert fake_s3.objects[result["report_key"]] == EXPECTED_CSV
    assert fake_s3.objects[result["xls_key"]] == EXPECTED_CSV


def test_handler_is_idempotent_re_aggregating_from_scratch(fake_s3):
    first = handler_report.handler({"ns": "demo"}, None)
    second = handler_report.handler({"ns": "demo"}, None)

    assert first == second
    assert fake_s3.objects[second["report_key"]] == EXPECTED_CSV


def test_handler_only_reads_the_namespace_prefix(fake_s3):
    fake_s3.objects["parsed/other/CUSTBILL_OTHER_001.psv"] = (
        b"C000000009|OTTER LTD|2025-01-01|99999.99|USD|01\n"
    )

    result = handler_report.handler({"ns": "demo"}, None)

    assert fake_s3.objects[result["report_key"]] == EXPECTED_CSV


def test_handler_ignores_non_psv_objects(fake_s3):
    fake_s3.objects["parsed/demo/_SUCCESS"] = b"not a psv\n"

    result = handler_report.handler({"ns": "demo"}, None)

    assert result["files_aggregated"] == 2
    assert fake_s3.objects[result["report_key"]] == EXPECTED_CSV


def test_handler_does_not_clobber_a_report_built_from_more_files(fake_s3, monkeypatch):
    # one execution runs per landed file; a slow run whose listing lagged behind the
    # bucket must not overwrite the complete report a faster sibling published
    complete = handler_report.handler({"ns": "demo"}, None)
    real_keys = handler_report._parsed_keys
    calls = {"n": 0}

    def lagging_keys(client, bucket, prefix):
        # every aggregation listing lags, every verification listing sees the truth,
        # so the run exhausts its retries still holding an incomplete aggregate
        calls["n"] += 1
        keys = real_keys(client, bucket, prefix)
        return keys[:1] if calls["n"] % 2 else keys

    monkeypatch.setattr(handler_report, "_parsed_keys", lagging_keys)

    partial = handler_report.handler({"ns": "demo"}, None)

    assert partial["published"] is False
    assert partial["files_aggregated"] == 1
    assert fake_s3.objects[complete["report_key"]] == EXPECTED_CSV


def test_handler_republishes_after_a_parsed_file_is_removed(fake_s3):
    # the ops beat: once an operator deletes a bad parsed file the report must be
    # corrected on the next run, not blocked until the date stamp rolls over
    first = handler_report.handler({"ns": "demo"}, None)
    assert fake_s3.objects[first["report_key"]] == EXPECTED_CSV
    del fake_s3.objects["parsed/demo/CUSTBILL_FIX_002.psv"]

    corrected = handler_report.handler({"ns": "demo"}, None)

    assert corrected["published"] is True
    assert corrected["files_aggregated"] == 1
    assert fake_s3.objects[corrected["report_key"]] != EXPECTED_CSV


def test_handler_reaggregates_when_a_sibling_parse_lands_mid_run(fake_s3, monkeypatch):
    late_key = "parsed/demo/CUSTBILL_FIX_003.psv"
    late_body = b"C000000099|OTTER LTD|2025-01-01|10.00|USD|01\n"
    real_keys = handler_report._parsed_keys
    calls = {"n": 0}

    def racing_keys(client, bucket, prefix):
        calls["n"] += 1
        keys = real_keys(client, bucket, prefix)
        if calls["n"] == 1:  # the late file lands right after the first listing
            fake_s3.objects[late_key] = late_body
        return keys

    monkeypatch.setattr(handler_report, "_parsed_keys", racing_keys)

    result = handler_report.handler({"ns": "demo"}, None)

    assert result["files_aggregated"] == 3
    assert fake_s3.objects[result["report_key"]] != EXPECTED_CSV


def test_handler_requires_a_namespace(fake_s3):
    with pytest.raises(ValueError):
        handler_report.handler({}, None)


@pytest.mark.parametrize("ns", ["../other", "a/b", ".."])
def test_handler_rejects_a_namespace_that_is_not_one_path_segment(fake_s3, ns):
    with pytest.raises(ValueError):
        handler_report.handler({"ns": ns}, None)


def test_report_stamp_uses_tz_env_var(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    utc_stamp = handler_report.report_stamp()
    assert len(utc_stamp) == 8 and utc_stamp.isdigit()

    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    ahead = handler_report.report_stamp()
    monkeypatch.setenv("TZ", "Pacific/Niue")
    behind = handler_report.report_stamp()
    assert ahead >= behind
    assert os.environ["TZ"] == "Pacific/Niue"
