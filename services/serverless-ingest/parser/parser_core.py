"""Byte-exact port of etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh.

Reproduces the sed/cut/paste/awk chain byte-for-byte, including its quirks:
HDR/TRL lines are stripped, every remaining line is sliced on the CBCUST01
copybook columns (1-10, 11-40, 41-48, 49-60, 61-63, 64-65), trailing spaces
are trimmed from fields 1/2/5 only, the implied-decimal amount is emitted as
%.2f of value/100 (awk numeric coercion: empty/garbage -> 0), and the date is
reformatted YYYYMMDD -> YYYY-MM-DD with no validity check. Malformed records
pass through; anomalies are attributed on the side, never in the bytes.

The legacy chain pastes the six cut streams with '|' and re-splits with
awk -F'|', so a '|' byte already inside a fixed-width field shifts awk's
field numbering (the date/amount transforms land on the wrong columns and
extra columns are emitted). That re-split is emulated exactly, and such
records are attributed as A-extra-delimiter in the ledger.

All processing is on raw bytes: input is a mainframe extract and is not
guaranteed to be valid UTF-8.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RECORD_LEN = 65

# awk's numeric coercion of a string: the longest numeric prefix, else 0.
_AWK_NUMBER = re.compile(rb"^[ \t]*([-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?)")


@dataclass
class ParseResult:
    psv: bytes = b""
    record_count: int = 0
    trailer_count: int | None = None
    anomalies: list[str] = field(default_factory=list)


def _awk_num(raw: bytes) -> float:
    m = _AWK_NUMBER.match(raw)
    return float(m.group(1)) if m else 0.0


def _rtrim_spaces(raw: bytes) -> bytes:
    return raw.rstrip(b" ")


def _valid_date(yyyymmdd: bytes) -> bool:
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        return False
    year = int(yyyymmdd[0:4])
    month = int(yyyymmdd[4:6])
    day = int(yyyymmdd[6:8])
    if not 1 <= month <= 12:
        return False
    days = [31, 29 if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0 else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return 1 <= day <= days


def parse_custbill(data: bytes) -> ParseResult:
    result = ParseResult()
    anomalies: set[str] = set()

    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()

    trailer_raw: bytes | None = None
    out: list[bytes] = []
    for line in lines:
        if line.startswith(b"HDR"):
            continue
        if line.startswith(b"TRL"):
            if trailer_raw is None:
                # cut -c4-13 | sed 's/^0*//'
                trailer_raw = line[3:13].lstrip(b"0")
            continue

        f1 = line[0:10]
        f2 = line[10:40]
        f3 = line[40:48]
        f4 = line[48:60]
        f5 = line[60:63]
        f6 = line[63:65]

        if len(line) < RECORD_LEN:
            anomalies.add("A-short-record")
        try:
            line.decode("utf-8")
        except UnicodeDecodeError:
            anomalies.add("A-nonutf8-byte")
        if f3 != b"" and not _valid_date(f3):
            anomalies.add("A-invalid-date")

        # paste -d'|' then awk -F'|': embedded pipes shift the field numbering.
        fields = b"|".join([f1, f2, f3, f4, f5, f6]).split(b"|")
        if len(fields) != 6:
            anomalies.add("A-extra-delimiter")
        fields[0] = _rtrim_spaces(fields[0])
        fields[1] = _rtrim_spaces(fields[1])
        fields[4] = _rtrim_spaces(fields[4])
        fields[3] = b"%.2f" % (_awk_num(fields[3]) / 100)
        d = fields[2]
        fields[2] = d[0:4] + b"-" + d[4:6] + b"-" + d[6:8]
        rendered = b"|".join(fields)
        out.append(rendered + b"\n")
        if rendered != b"":  # grep -c . semantics
            result.record_count += 1

    result.psv = b"".join(out)
    if trailer_raw is not None:
        result.trailer_count = int(trailer_raw) if trailer_raw.isdigit() else 0
        if result.trailer_count != result.record_count:
            anomalies.add("A-trailer-mismatch")
    result.anomalies = sorted(anomalies)
    return result
