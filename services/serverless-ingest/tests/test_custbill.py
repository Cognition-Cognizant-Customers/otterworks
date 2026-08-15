from __future__ import annotations

from pathlib import Path

import custbill


FIXTURES = Path(__file__).parent / "fixtures"


def test_fixture_is_byte_identical_to_legacy_golden() -> None:
    source = (FIXTURES / "CUSTBILL_SAMPLE_001.dat").read_text()
    expected = (FIXTURES / "CUSTBILL_SAMPLE_001.psv").read_bytes()

    output, count = custbill.parse_body(source)

    assert output == expected
    assert count == 5


def test_non_utf8_bytes_round_trip_through_name_field() -> None:
    source = (
        b"C000000001"
        + b"NAME\xa0WITH" + b" " * 21
        + b"20250102"
        + b"000000000100"
        + b"USD01\n"
    )

    output, count = custbill.parse_body(source)

    assert output == b"C000000001|NAME\xa0WITH|2025-01-02|1.00|USD|01\n"
    assert count == 1


def test_vertical_tab_inside_record_does_not_split_the_record() -> None:
    source = (
        b"C000000001"
        + b"NAME\x0bWITH" + b" " * 21
        + b"20250102"
        + b"000000000100"
        + b"USD01\n"
    )

    output, count = custbill.parse_body(source)

    assert output == b"C000000001|NAME\x0bWITH|2025-01-02|1.00|USD|01\n"
    assert count == 1


def _record(
    *,
    customer: str = "C000000001",
    name: str = "NAME",
    date: str = "20250102",
    amount: str = "000000000100",
    currency: str = "USD",
    rec_type: str = "01",
) -> str:
    return f"{customer:<10}{name:<30}{date}{amount}{currency:<3}{rec_type}"


def test_parse_line_preserves_trailing_spaces_in_date_and_record_type() -> None:
    line = _record(date="202501  ", rec_type="0 ") + "   "

    assert custbill.parse_line(line)[2] == "2025-01-  "
    assert custbill.parse_line(line)[5] == "0 "


def test_amount_coercion_matches_mawk_prefix_rules() -> None:
    cases = {
        "0000ABCD1234": "0.00",
        "  1234      ": "12.34",
        "12ab34567890": "0.12",
        "-00000012345": "-123.45",
        "+00000000100": "1.00",
        "     .5     ": "0.01",
        "1e3         ": "10.00",
        "0x1A        ": "0.26",
        "            ": "0.00",
    }

    for amount, expected in cases.items():
        assert custbill.parse_line(_record(amount=amount))[3] == expected


def test_short_records_slice_without_padding_and_malformed_records_pass_through() -> None:
    fields = custbill.parse_line("CUST")

    assert fields == ("CUST", "", "--", "0.00", "", "")


def test_headers_and_trailers_are_dropped_and_empty_body_is_empty() -> None:
    body = "HDR anything\n" + _record() + "\nTRL0000000001" + " " * 52 + "\n"

    output, count = custbill.parse_body(body)

    assert output.count(b"\n") == 1
    assert count == 1
    assert custbill.parse_body("HDR only\nTRL0000000000\n") == (b"", 0)


def test_trailer_count_matches_legacy_extraction() -> None:
    assert custbill.trailer_count("TRL0000000005" + " " * 52) == 5
    assert custbill.trailer_count("TRL0000000000" + " " * 52) is None
    assert custbill.trailer_count("TRLnotnumeric" + " " * 52) is None
    assert custbill.trailer_count("CUST anything\n") is None
