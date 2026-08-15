"""Pure CUSTBILL fixed-width parsing logic."""

from __future__ import annotations

import re

_DECIMAL_PREFIX = re.compile(
    r"[+-]?(?:(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)"
)
_HEX_PREFIX = re.compile(
    r"[+-]?0[xX](?:(?:[0-9A-Fa-f]+\.[0-9A-Fa-f]*|\.[0-9A-Fa-f]+|[0-9A-Fa-f]+)"
    r"(?:[pP][+-]?\d+)?)"
)


def _awk_number(value: str) -> float:
    """Parse the numeric prefix accepted by mawk's string-to-number coercion."""
    stripped = value.lstrip()
    hex_match = _HEX_PREFIX.match(stripped)
    decimal_match = _DECIMAL_PREFIX.match(stripped)
    match = hex_match or decimal_match
    if match is None:
        return 0.0
    token = match.group(0)
    try:
        if token.lower().lstrip("+-").startswith("0x"):
            return float.fromhex(token)
        return float(token)
    except ValueError:
        return 0.0


def parse_line(line: str) -> tuple[str, str, str, str, str, str] | None:
    """Parse one input line, returning PSV fields or None for HDR/TRL lines."""
    if line.startswith(("HDR", "TRL")):
        return None

    cust_id = line[0:10].rstrip(" ")
    cust_name = line[10:40].rstrip(" ")
    raw_date = line[40:48]
    raw_amount = line[48:60]
    currency = line[60:63].rstrip(" ")
    rec_type = line[63:65]

    bill_date = (
        raw_date[0:4]
        + "-"
        + raw_date[4:6]
        + "-"
        + raw_date[6:8]
    )
    amount = f"{_awk_number(raw_amount) / 100:.2f}"
    return cust_id, cust_name, bill_date, amount, currency, rec_type


def _text_body(body: str | bytes) -> str:
    if isinstance(body, bytes):
        return body.decode("latin-1")
    return body


def _lines(body: str) -> list[str]:
    if not body:
        return []
    lines = body.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def parse_records(body: str | bytes) -> list[tuple[str, str, str, str, str, str]]:
    """Parse all non-header/trailer records in input order."""
    records = []
    for line in _lines(_text_body(body)):
        fields = parse_line(line)
        if fields is not None:
            records.append(fields)
    return records


def parse_body(body: str | bytes) -> tuple[bytes, int]:
    """Return legacy-compatible PSV bytes and the number of output records."""
    records = parse_records(body)
    output = "".join(f"{'|'.join(fields)}\n" for fields in records)
    return output.encode("latin-1"), len(records)


def trailer_count(body: str | bytes) -> int | None:
    """Extract the first TRL count using the legacy columns and zero stripping."""
    for line in _lines(_text_body(body)):
        if line.startswith("TRL"):
            value = line[3:13].lstrip("0")
            if value and all("0" <= char <= "9" for char in value):
                return int(value)
            return None
    return None
