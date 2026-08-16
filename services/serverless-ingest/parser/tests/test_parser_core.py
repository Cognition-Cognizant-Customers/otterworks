"""Golden-byte contract tests for the legacy CUSTBILL parser port.

Fixtures were recorded from the deterministic legacy run
(TP_FAKETIME='2026-01-15 00:00:00' scripts/tp-run-deterministic.sh
etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh) and match the md5s
pinned in docs/tech-partnerships/contracts/aws-parser.contract.json.
"""
import hashlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from parser_core import parse_custbill  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

GOLDEN_MD5 = {
    "CUSTBILL_DEMO_001": (
        "f304679dff6190de3206ccc15466428c",
        "29f0e7a8bc25eb444c050b3f1e8e8a9d",
    ),
    "CUSTBILL_DEMO_002": (
        "5153ba871c74d8d2d518021f63b36d07",
        "665fee687aaaa0be43f3db23021e340a",
    ),
    "CUSTBILL_DEMO_ANOM": (
        "11eb3d1a3cf99ad46d66d3c65c0add01",
        "b098ae00d55d3974f40133b0d607a1b8",
    ),
}


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def test_fixture_inputs_match_contract_md5s():
    for base, (input_md5, _) in GOLDEN_MD5.items():
        assert md5((FIXTURES / f"{base}.dat").read_bytes()) == input_md5


def test_byte_identical_psv_all_files():
    for base, (_, output_md5) in GOLDEN_MD5.items():
        data = (FIXTURES / f"{base}.dat").read_bytes()
        result = parse_custbill(data)
        golden = (FIXTURES / f"{base}.psv").read_bytes()
        assert result.psv == golden, f"{base}.psv bytes diverge from legacy golden"
        assert md5(result.psv) == output_md5


def test_clean_files_have_no_anomalies_and_counts_reconcile():
    for base in ("CUSTBILL_DEMO_001", "CUSTBILL_DEMO_002"):
        result = parse_custbill((FIXTURES / f"{base}.dat").read_bytes())
        assert result.anomalies == []
        assert result.record_count == 50
        assert result.trailer_count == 50


def test_anomaly_file_detects_exactly_the_planted_set():
    result = parse_custbill((FIXTURES / "CUSTBILL_DEMO_ANOM.dat").read_bytes())
    assert result.anomalies == [
        "A-invalid-date",
        "A-nonutf8-byte",
        "A-short-record",
        "A-trailer-mismatch",
    ]
    assert result.record_count == 3
    assert result.trailer_count == 5


def test_anomaly_bytes_still_pass_through_legacy_quirks():
    result = parse_custbill((FIXTURES / "CUSTBILL_DEMO_ANOM.dat").read_bytes())
    lines = result.psv.split(b"\n")
    assert lines[0].split(b"|")[2] == b"2024-13-85"  # invalid date reformatted, not checked
    assert b"\xa3" in lines[1]  # non-UTF-8 byte re-emitted unchanged
    assert lines[2] == b"C000000903|SHORTY GMBH|--|0.00||"  # short record -> empty slices


def test_empty_body_hdr_trl_only_writes_empty_psv():
    hdr = b"HDR CUSTBILL EXTRACT" + b" " * 45 + b"\n"
    trl = b"TRL0000000000" + b" " * 52 + b"\n"
    result = parse_custbill(hdr + trl)
    assert result.psv == b""
    assert result.record_count == 0
    assert result.trailer_count == 0
    assert result.anomalies == []


def test_embedded_pipe_reproduces_legacy_awk_resplit():
    # Recorded from the legacy chain: paste -d'|' | awk -F'|' re-splits an
    # embedded '|' so field numbering shifts and 7 columns come out.
    hdr = b"HDR CUSTBILL EXTRACT NS=PIPE      FILE=001"
    hdr = hdr + b" " * (65 - len(hdr)) + b"\n"
    r1 = (b"C000000801" + b"PIPE|CO   LTD".ljust(30) + b"20240102"
          + b"000000012345" + b"USD" + b"01" + b"\n")
    r2 = (b"C000000802" + b"NORMAL CORP".ljust(30) + b"20240103"
          + b"000000054321" + b"EUR" + b"02" + b"\n")
    trl = b"TRL0000000002" + b" " * 52 + b"\n"
    result = parse_custbill(hdr + r1 + r2 + trl)
    assert result.psv == (
        b"C000000801|PIPE|CO  - L-TD|202401.02|000000012345|USD|01\n"
        b"C000000802|NORMAL CORP|2024-01-03|543.21|EUR|02\n"
    )
    assert result.anomalies == ["A-extra-delimiter"]
    assert result.record_count == 2
    assert result.trailer_count == 2


def test_parse_is_deterministic_on_rerun():
    data = (FIXTURES / "CUSTBILL_DEMO_ANOM.dat").read_bytes()
    first = parse_custbill(data)
    second = parse_custbill(data)
    assert first.psv == second.psv
    assert first.anomalies == second.anomalies
    assert (first.record_count, first.trailer_count) == (
        second.record_count,
        second.trailer_count,
    )
