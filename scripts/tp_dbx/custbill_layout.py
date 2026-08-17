"""CBCUST01 fixed-width layout: one validating parse of a CUSTBILL extract.

Replaces the three-pass sed/cut/paste/awk slicing in
etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh with a single positional
pass that fails closed. Layout (copybook CBCUST01):

  pos  1-10   CUST-ID    PIC X(10)
  pos 11-40   CUST-NAME  PIC X(30)
  pos 41-48   BILL-DATE  PIC 9(8)    YYYYMMDD
  pos 49-60   BILL-AMT   PIC 9(10)V99 implied decimal -> integer cents
  pos 61-63   CURRENCY   PIC X(3)
  pos 64-65   REC-TYPE   PIC X(2)    01=invoice 02=credit

Policy comes from docs/tech-partnerships/contracts/parse_custbill_fixedwidth.json:
bytes are read with a lossless single-byte decode (never a strict UTF-8 decode
that could abort a run), a record whose bytes are not valid UTF-8 is quarantined
with an encoding reason rather than repaired, a record that does not match the
layout is quarantined rather than allowed to fail open into a plausible row, and
a trailer count that disagrees with the record count fails the whole file.

Deliberately dependency-free and Spark-free: the same text runs in the local
fixture harness and, inlined, inside the Databricks notebook task.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field

RECORD_LENGTH = 65
FIELDS = {
    "cust_id": (0, 10),
    "cust_name": (10, 40),
    "bill_date": (40, 48),
    "amount": (48, 60),
    "currency": (60, 63),
    "record_type": (63, 65),
}
RECORD_TYPES = ("01", "02")

REASONS = (
    "bad_record_length",
    "encoding_invalid_byte",
    "missing_cust_id",
    "missing_cust_name",
    "invalid_date_format",
    "invalid_calendar_date",
    "non_numeric_amount",
    "invalid_currency",
    "invalid_record_type",
    "trailer_count_mismatch",
    "missing_trailer",
    "file_failed_missing_trailer",
    "file_failed_trailer_mismatch",
)

DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


@dataclass
class Record:
    source_file: str
    source_line: int
    cust_id: str
    cust_name: str
    bill_date: str
    amount_cents: int
    currency: str
    record_type: str


@dataclass
class Reject:
    source_file: str
    source_line: int
    raw_bytes_base64: str
    raw_line: str
    reason_code: str
    detail: str


@dataclass
class FileResult:
    source_file: str
    records: list = field(default_factory=list)
    rejects: list = field(default_factory=list)
    body_count: int = 0
    trailer_count: int = -1
    failed: bool = False


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def valid_calendar_date(year: int, month: int, day: int) -> bool:
    if not 1 <= month <= 12:
        return False
    limit = DAYS_IN_MONTH[month - 1]
    if month == 2 and _is_leap(year):
        limit = 29
    return 1 <= day <= limit


def decode_line(raw: bytes) -> tuple[str, bool]:
    """Lossless single-byte decode plus a UTF-8 validity verdict.

    latin-1 cannot fail, so a run can never abort on a bad byte; the boolean
    reports whether the bytes were valid UTF-8 so a suspect record can be
    quarantined with its original bytes preserved instead of repaired.
    """
    try:
        raw.decode("utf-8")
        return raw.decode("latin-1"), True
    except UnicodeDecodeError:
        return raw.decode("latin-1"), False


def _reject(source_file: str, line_no: int, raw: bytes, text: str, reason: str, detail: str = "") -> Reject:
    return Reject(
        source_file=source_file,
        source_line=line_no,
        raw_bytes_base64=base64.b64encode(raw).decode("ascii"),
        raw_line=text,
        reason_code=reason,
        detail=detail,
    )


def parse_record(source_file: str, line_no: int, raw: bytes):
    """Return (Record, None) or (None, Reject) for one body line."""
    text, utf8_ok = decode_line(raw)
    if not utf8_ok:
        return None, _reject(source_file, line_no, raw, text, "encoding_invalid_byte",
                             "line bytes are not valid UTF-8; stored verbatim")
    if len(raw) != RECORD_LENGTH:
        return None, _reject(source_file, line_no, raw, text, "bad_record_length",
                             f"expected {RECORD_LENGTH} bytes, got {len(raw)}")

    def slice_field(name: str) -> str:
        start, end = FIELDS[name]
        return text[start:end]

    cust_id = slice_field("cust_id").rstrip(" ")
    if not cust_id:
        return None, _reject(source_file, line_no, raw, text, "missing_cust_id", "CUST-ID is blank")
    cust_name = slice_field("cust_name").rstrip(" ")
    if not cust_name:
        return None, _reject(source_file, line_no, raw, text, "missing_cust_name", "CUST-NAME is blank")

    date_field = slice_field("bill_date")
    if not (len(date_field) == 8 and date_field.isascii() and date_field.isdecimal()):
        return None, _reject(source_file, line_no, raw, text, "invalid_date_format",
                             f"BILL-DATE {date_field!r} is not 8 digits")
    year, month, day = int(date_field[:4]), int(date_field[4:6]), int(date_field[6:8])
    if not valid_calendar_date(year, month, day):
        return None, _reject(source_file, line_no, raw, text, "invalid_calendar_date",
                             f"BILL-DATE {date_field} is not a calendar date")

    amount_field = slice_field("amount")
    if not (len(amount_field) == 12 and amount_field.isascii() and amount_field.isdecimal()):
        return None, _reject(source_file, line_no, raw, text, "non_numeric_amount",
                             f"BILL-AMT {amount_field!r} is not 12 digits")
    amount_cents = int(amount_field)

    currency = slice_field("currency")
    if not (len(currency) == 3 and currency.isascii() and currency.isalpha() and currency.isupper()):
        return None, _reject(source_file, line_no, raw, text, "invalid_currency",
                             f"CURRENCY {currency!r} is not three uppercase letters")

    record_type = slice_field("record_type")
    if record_type not in RECORD_TYPES:
        return None, _reject(source_file, line_no, raw, text, "invalid_record_type",
                             f"REC-TYPE {record_type!r} is not one of {RECORD_TYPES}")

    return Record(
        source_file=source_file,
        source_line=line_no,
        cust_id=cust_id,
        cust_name=cust_name,
        bill_date=f"{date_field[:4]}-{date_field[4:6]}-{date_field[6:8]}",
        amount_cents=amount_cents,
        currency=currency,
        record_type=record_type,
    ), None


def split_lines(data: bytes) -> list:
    """Split on newlines, dropping only the final empty fragment.

    Byte-preserving: no decoding, no whitespace stripping, so a short or
    padded record is still visible to the length check.
    """
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def trailer_count(line: bytes) -> int:
    """TRL count occupies pos 4-13 (the legacy `cut -c4-13 | sed 's/^0*//'`)."""
    digits = line[3:13].decode("latin-1").strip()
    return int(digits) if digits.isascii() and digits.isdecimal() else -1


def parse_file(source_file: str, data: bytes) -> FileResult:
    result = FileResult(source_file=source_file)
    body: list = []
    trailers: list = []
    for index, raw in enumerate(split_lines(data), start=1):
        if raw.startswith(b"HDR"):
            continue
        if raw.startswith(b"TRL"):
            trailers.append((index, raw))
            continue
        body.append((index, raw))

    result.body_count = len(body)
    if trailers:
        result.trailer_count = trailer_count(trailers[-1][1])

    for line_no, raw in body:
        record, reject = parse_record(source_file, line_no, raw)
        if record is not None:
            result.records.append(record)
        else:
            result.rejects.append(reject)

    if not trailers:
        result.failed = True
        result.rejects.append(_reject(source_file, 0, b"", "", "missing_trailer",
                                      f"no TRL record; {result.body_count} records read"))
    elif result.trailer_count != result.body_count:
        # ETL-0187: the legacy script logged the two counts and moved on. Here the
        # file fails and both counts are recorded.
        result.failed = True
        line_no, raw = trailers[-1]
        text, _ = decode_line(raw)
        result.rejects.append(_reject(
            source_file, line_no, raw, text, "trailer_count_mismatch",
            f"trailer={result.trailer_count} records={result.body_count}"))

    if result.failed:
        accepted, result.records = result.records, []
        body_by_line = dict(body)
        failure_reason = (
            "file_failed_missing_trailer" if not trailers
            else "file_failed_trailer_mismatch"
        )
        failure_detail = (
            f"file rejected: no TRL record; records={result.body_count}"
            if not trailers
            else f"file rejected: trailer={result.trailer_count} records={result.body_count}"
        )
        for record in accepted:
            raw = body_by_line[record.source_line]
            result.rejects.append(Reject(
                source_file=source_file,
                source_line=record.source_line,
                raw_bytes_base64=base64.b64encode(
                    raw).decode("ascii"),
                raw_line=decode_line(raw)[0],
                reason_code=failure_reason,
                detail=failure_detail,
            ))
        result.rejects.sort(key=lambda item: (item.source_line, item.reason_code))
    return result
