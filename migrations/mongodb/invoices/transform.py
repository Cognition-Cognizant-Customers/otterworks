"""Pure transforms from the legacy Oracle invoice rows to Atlas documents.

Nothing here touches Oracle or Atlas: every function takes plain source rows
(dicts keyed by Oracle column name) and returns documents plus data-quality
findings. Anomalies are *reported*, never repaired: unparseable dates,
reversed service periods, non-numeric GL accounts and header/line customer
disagreements all survive into the target with a finding attached.
"""

import calendar
from datetime import datetime
from decimal import Decimal

from bson.decimal128 import Decimal128

SOURCE_TABLE = "OW_BILLING.INVOICE_HEADER"
ORPHAN_SOURCE_TABLE = "OW_BILLING.INVOICE_LINE"
QUARANTINE_MISSING_HEADER = "missing_header"

MONTHS = {name: i for i, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}

# Legacy line columns that are denormalized copies of customer data; the header
# carries `customerId`, so they are dropped from the embedded line.
DROPPED_LINE_COLUMNS = ("CUST_ID", "CUST_NO", "CUST_NAME")


class Findings:
    """Ordered data-quality ledger: counts plus a bounded id sample per kind."""

    def __init__(self, sample_size: int = 25) -> None:
        self.counts: dict[str, int] = {}
        self.samples: dict[str, list] = {}
        self._sample_size = sample_size

    def add(self, kind: str, ident, detail=None) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        sample = self.samples.setdefault(kind, [])
        if len(sample) < self._sample_size:
            sample.append({"id": ident, "detail": detail}
                          if detail is not None else {"id": ident})

    def merge(self, other: "Findings") -> None:
        for kind, count in other.counts.items():
            self.counts[kind] = self.counts.get(kind, 0) + count
            sample = self.samples.setdefault(kind, [])
            for entry in other.samples.get(kind, []):
                if len(sample) < self._sample_size:
                    sample.append(entry)

    def as_dict(self) -> dict:
        return {
            kind: {"count": count, "sample": self.samples.get(kind, [])}
            for kind, count in sorted(self.counts.items())
        }


def parse_legacy_date(value):
    """'DD-MON-YY' text -> datetime, or None when the string is not a date.

    Two-digit years are all 20xx: the estate's invoice dates span 2009-2025.
    """
    if value is None:
        return None
    parts = str(value).strip().upper().split("-")
    if len(parts) != 3:
        return None
    day, mon, year = parts
    if mon not in MONTHS or not day.isdigit() or not year.isdigit():
        return None
    try:
        return datetime(2000 + int(year), MONTHS[mon], int(day))
    except ValueError:
        return None


def parse_service_period(value):
    """'MMYYYY-MMYYYY' text -> {from, to} spanning whole months, else None.

    `to` is the last instant-free day of the closing month, so the period is
    inclusive of both endpoints' months.
    """
    if value is None:
        return None
    halves = str(value).strip().split("-")
    if len(halves) != 2:
        return None
    bounds = []
    for half in halves:
        if len(half) != 6 or not half.isdigit():
            return None
        month, year = int(half[:2]), int(half[2:])
        if not 1 <= month <= 12:
            return None
        bounds.append((year, month))
    (fy, fm), (ty, tm) = bounds
    return {
        "from": datetime(fy, fm, 1),
        "to": datetime(ty, tm, calendar.monthrange(ty, tm)[1]),
    }


def parse_gl_accounts(value):
    """'40001,40237' -> ([40001, 40237], []); junk tokens come back unparsed."""
    accounts, unparsed = [], []
    for token in (str(value).split(",") if value is not None else []):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            accounts.append(int(token))
        else:
            unparsed.append(token)
    return accounts, unparsed


def parse_posted(value):
    """CHAR(1) POSTED_YN -> bool; NULL (and anything unknown) -> None."""
    if value is None:
        return None
    flag = str(value).strip().upper()
    if flag == "Y":
        return True
    if flag == "N":
        return False
    return None


def decimal128(value):
    """Monetary/quantity value -> Decimal128, never a binary double."""
    if value is None:
        return None
    if isinstance(value, Decimal128):
        return value
    if isinstance(value, float):
        # oracledb is configured to fetch NUMBER as Decimal; a float here would
        # silently lose cents, so refuse it rather than round it.
        raise TypeError("refusing to build Decimal128 from a binary float")
    return Decimal128(Decimal(str(value)))


def _decimal(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


def as_int(value):
    """Oracle NUMBER (fetched as Decimal) -> int, so it stores as a BSON int."""
    if value is None:
        return None
    return int(Decimal(str(value)))


def bsonify(value):
    """Coerce a raw Oracle column value into a BSON-encodable one.

    `Decimal` has no BSON type; every NUMBER becomes `Decimal128` (never an
    int or a double) so a quarantined row keeps its source value and scale.
    """
    if isinstance(value, Decimal):
        return Decimal128(value)
    return value


def migration_envelope(ns: str, source_table: str, migrated_at: datetime) -> dict:
    return {"ns": ns, "sourceTable": source_table, "migratedAt": migrated_at}


def transform_line(row: dict, findings: Findings) -> dict:
    """One INVOICE_LINE row -> the embedded line subdocument."""
    line_id = row["LINE_ID"]
    accounts, unparsed = parse_gl_accounts(row.get("GL_ACCT_CSV"))
    if unparsed:
        findings.add("unparsed_gl_account", line_id, unparsed)

    period = parse_service_period(row.get("SERVICE_PERIOD"))
    if period is None:
        if row.get("SERVICE_PERIOD") is not None:
            findings.add("unparseable_service_period", line_id,
                         row.get("SERVICE_PERIOD"))
    elif period["from"] > period["to"]:
        findings.add("reversed_service_period", line_id,
                     row.get("SERVICE_PERIOD"))

    line = {
        "lineId": line_id,
        "lineNo": as_int(row.get("LINE_NO")),
        # LINE_TYPE_CD has no CODES('LINE_TYPE') set in the legacy estate, so the
        # magic number is carried across as-is rather than invented.
        "type": as_int(row.get("LINE_TYPE_CD")),
        "description": row.get("ITEM_DESC"),
        "qty": decimal128(row.get("QTY")),
        "unitPrice": decimal128(row.get("UNIT_PRICE")),
        "amount": decimal128(row.get("AMOUNT")),
        "taxAmount": decimal128(row.get("TAX_AMT")),
        "glAccounts": accounts,
        "srcSystem": row.get("SRC_SYSTEM"),
    }
    if period is not None:
        line["servicePeriod"] = period
    if unparsed:
        line["glAccountsUnparsed"] = unparsed
    posted = parse_posted(row.get("POSTED_YN"))
    if posted is not None:
        # POSTED_YN is NULL for part of the feed: the field is absent rather
        # than defaulted, so "never posted" stays distinguishable from "unknown".
        line["posted"] = posted
    return line


def transform_invoice(header: dict, lines: list[dict], status_codes: dict,
                      ns: str, migrated_at: datetime) -> tuple[dict, Findings]:
    """INVOICE_HEADER row + its INVOICE_LINE rows -> one `invoices` document."""
    findings = Findings()
    invoice_id = header["INVOICE_ID"]

    invoice_date = parse_legacy_date(header.get("INVOICE_DT"))
    if invoice_date is None and header.get("INVOICE_DT") is not None:
        findings.add("unparseable_invoice_date", invoice_id,
                     header.get("INVOICE_DT"))
    due_date = parse_legacy_date(header.get("DUE_DT"))
    if due_date is None and header.get("DUE_DT") is not None:
        findings.add("unparseable_due_date", invoice_id, header.get("DUE_DT"))

    status_cd = as_int(header.get("STATUS_CD"))
    status = status_codes.get(status_cd)
    if status is None:
        if status_cd is None:
            # STATUS_CD is nullable in the legacy estate: a missing status stays
            # missing rather than becoming an invented status string.
            findings.add("null_status_code", invoice_id)
        else:
            findings.add("unmapped_status_code", invoice_id, status_cd)
            status = str(status_cd)

    embedded, line_total = [], Decimal("0")
    for row in lines:
        if row.get("CUST_ID") != header.get("CUST_ID"):
            findings.add("line_customer_mismatch", row["LINE_ID"],
                         {"lineCustId": row.get("CUST_ID"),
                          "headerCustId": header.get("CUST_ID")})
        embedded.append(transform_line(row, findings))
        line_total += _decimal(row.get("AMOUNT"))

    doc = {
        "_id": invoice_id,
        "invoiceNo": header.get("INVOICE_NO"),
        "customerId": header.get("CUST_ID"),
        "tenantId": header.get("TENANT_ID"),
        "status": status,
        "invoiceDate": invoice_date,
        "dueDate": due_date,
        # `totalAmount` and `lineTotal` disagree in the source estate; both are
        # kept verbatim and neither is reconciled into the other.
        "totalAmount": decimal128(header.get("TOTAL_AMT")),
        "lineCount": len(embedded),
        "lineTotal": Decimal128(line_total.quantize(Decimal("0.01"))),
        "lines": embedded,
        "legacy": {"batchNo": as_int(header.get("BATCH_NO"))},
        "_migration": migration_envelope(ns, SOURCE_TABLE, migrated_at),
    }
    return doc, findings


def transform_orphan(row: dict, ns: str, migrated_at: datetime) -> dict:
    """A header-less INVOICE_LINE row -> quarantine document (raw, not repaired)."""
    return {
        "_id": row["LINE_ID"],
        "lineId": row["LINE_ID"],
        "amount": decimal128(row.get("AMOUNT")),
        "quarantine_reason": QUARANTINE_MISSING_HEADER,
        "raw": {k: bsonify(v) for k, v in row.items()},
        "_migration": migration_envelope(ns, ORPHAN_SOURCE_TABLE, migrated_at),
    }
