"""Pure CUSTBILL fixed-width parsing logic."""

from __future__ import annotations

import re

_C_LOCALE_BLANKS = " \t\n\v\f\r"
_DECIMAL_PREFIX = re.compile(
    r"[+-]?(?:(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)"
)
_HEX_PREFIX = re.compile(
    r"[+-]?0[xX](?:(?:[0-9A-Fa-f]+\.[0-9A-Fa-f]*|\.[0-9A-Fa-f]+|[0-9A-Fa-f]+)"
    r"(?:[pP][+-]?\d+)?)"
)


def _awk_number(value: str) -> float:
    """Parse the numeric prefix accepted by mawk's string-to-number coercion."""
    stripped = value.lstrip(_C_LOCALE_BLANKS)
    hex_match = _HEX_PREFIX.match(stripped)
    decimal_match = _DECIMAL_PREFIX.match(stripped)
    match = hex_match or decimal_match
    if match is None:
        return 0.0
    token = match.group(0)
    try:
        if token.lower().lstrip("+-").startswith("0x"):
            number = float.fromhex(token)
        else:
            number = float(token)
        return 0.0 if number == 0 else number
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


def latin1_lines(body: str | bytes) -> list[str]:
    """Split a latin-1 body the way the legacy readers did: on "\\n" only."""
    return _lines(_text_body(body))


def _raw_fields(line: str) -> tuple[str, str, str, str, str, str]:
    return (
        line[0:10],
        line[10:40],
        line[40:48],
        line[48:60],
        line[60:63],
        line[63:65],
    )


def parse_records(body: str | bytes) -> list[tuple[str, str, str, str, str, str]]:
    """Parse all non-header/trailer records in input order."""
    records = []
    for line in _lines(_text_body(body)):
        fields = parse_line(line)
        if fields is not None:
            records.append(fields)
    return records


def _legacy_output_line(line: str) -> str | None:
    if line.startswith(("HDR", "TRL")):
        return None

    fields = "|".join(_raw_fields(line)).split("|")
    for index in (0, 1, 4):
        fields[index] = fields[index].rstrip(" ")
    fields[2] = (
        fields[2][0:4]
        + "-"
        + fields[2][4:6]
        + "-"
        + fields[2][6:8]
    )
    fields[3] = f"{_awk_number(fields[3]) / 100:.2f}"
    return "|".join(fields)


def parse_body(body: str | bytes) -> tuple[bytes, int]:
    """Return legacy-compatible PSV bytes and the number of output records."""
    output_lines = []
    for line in _lines(_text_body(body)):
        output_line = _legacy_output_line(line)
        if output_line is not None:
            output_lines.append(output_line)
    output = "".join(f"{line}\n" for line in output_lines)
    return output.encode("latin-1"), len(output_lines)


def trailer_count(body: str | bytes) -> int | None:
    """Extract the first TRL count using the legacy columns and zero stripping."""
    for line in _lines(_text_body(body)):
        if line.startswith("TRL"):
            value = line[3:13].rstrip(" ")
            if value and all("0" <= char <= "9" for char in value):
                return int(value)
            return None
    return None
