import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from custbill import finance_report, parse_file, parse_line

LINE = "C000123456ACME HOLDINGS                 20250401000000123456USD01"


def test_parse_line():
    assert parse_line(LINE) == "C000123456|ACME HOLDINGS|2025-04-01|1234.56|USD|01"


def test_parse_file_strips_hdr_trl():
    text = "HDR CUSTBILL EXTRACT\n" + LINE + "\nTRL0000000001\n"
    assert parse_file(text) == ["C000123456|ACME HOLDINGS|2025-04-01|1234.56|USD|01"]


def test_parse_file_splits_on_newline_only():
    # control bytes like \x0b stay inside the record, like sed/cut
    dirty = LINE[:15] + "\x0b" + LINE[16:]
    text = "HDR CUSTBILL EXTRACT\n" + dirty + "\nTRL0000000001\n"
    records = parse_file(text)
    assert len(records) == 1
    assert "\x0b" in records[0]


def test_finance_report():
    lines = [
        "C1|A|2025-01-01|10.00|USD|01",
        "C2|B|2025-01-02|5.50|USD|01",
        "C3|C|2025-01-03|2.25|EUR|02",
    ]
    assert finance_report(lines) == (
        "Currency,RecordType,RecordCount,TotalAmount\n"
        "EUR,CREDIT,1,2.25\n"
        "USD,INVOICE,2,15.50\n"
    )


def test_finance_report_empty():
    assert finance_report([]) == "Currency,RecordType,RecordCount,TotalAmount\n"
