"""Pure transformer: one `CUSTOMER_MASTER` row (+ its EAV rows) -> one document.

No I/O, no globals, no wall-clock reads — every input is an argument, so the
whole mapping is unit-testable (`test_transform.py`). The legacy horror this
undoes is described in `docs/tech-partnerships/contracts/mongo-customers.md`.

Preservation rules (anomalies are reported, never repaired or dropped):

* a value that cannot be parsed is kept verbatim under `_quarantine.<COLUMN>`
  and the parsed field is omitted — the customer document is still produced,
  so counts and checksums stay equal to the source;
* sparse columns are omitted rather than stored as `null`, and empty
  repeating-group slots produce no array entry.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

LEGACY_DATE_RE = re.compile(r"^(\d{2})-([A-Z]{3})-(\d{2})$")
ID_RE = re.compile(r"^\d+$")
CODE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Two-digit years: Oracle 'RR' semantics for a current year in 2000-2049, so
# 00-49 is 2000s and 50-99 is 1900s.
YEAR_PIVOT = 49

SOURCE_TABLE = "OW_BILLING.CUSTOMER_MASTER"

DATE_FIELDS = (
    ("SIGNUP_DT", "signup"),
    ("LAST_ACTIVITY_DT", "lastActivity"),
    ("LAST_INVOICE_DT", "lastInvoice"),
    ("LAST_PAYMENT_DT", "lastPayment"),
    ("TERMINATE_DT", "terminate"),
)

FLAG_FIELDS = (
    ("TAX_EXEMPT_YN", "taxExempt"),
    ("CREDIT_HOLD_YN", "creditHold"),
    ("DUNNING_EXEMPT_YN", "dunningExempt"),
    ("VIP_YN", "vip"),
)

BALANCE_FIELDS = (
    ("CUR_BAL_AMT", "current"),
    ("PAST_DUE_AMT", "pastDue"),
    ("YTD_BILLED_AMT", "ytdBilled"),
    ("LTD_BILLED_AMT", "ltdBilled"),
    ("YTD_PAID_AMT", "ytdPaid"),
    ("CREDIT_LIMIT_AMT", "creditLimit"),
)

CLASSIFICATION_FIELDS = (
    ("SEGMENT_CD", "segment"),
    ("REGION_CD", "region"),
    ("TERRITORY_CD", "territory"),
    ("CHANNEL_CD", "channel"),
    ("RATE_CLASS_CD", "rateClass"),
)

# Comma-separated id lists in VARCHAR2. `tokens=True` allows alphanumeric
# promo codes; the others must be clean numeric id lists.
CSV_FIELDS = (
    ("RELATED_ACCT_IDS", "relatedAccountIds", False),
    ("CHILD_ACCT_IDS", "childAccountIds", False),
    ("PROMO_CODES_CSV", "promoCodes", True),
)

SPARSE_PREFIXES = ("FLAG_", "UDF_")

KIND_DIRTY_DATE = "dirty_dates"
KIND_MALFORMED_CSV = "malformed_csv_lists"


@dataclass
class Quarantine:
    """A single field that could not be parsed; the raw value is preserved."""

    field: str
    kind: str
    raw: str


@dataclass
class Transformed:
    doc: dict
    quarantine: list = field(default_factory=list)
    eav_rows_consumed: int = 0
    attr_keys_folded: int = 0
    attr_conflicts: int = 0


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _put(target: dict, key: str, value) -> None:
    """Set `key` only when the value is present — sparse columns are omitted."""
    if _blank(value) or (isinstance(value, (list, dict)) and not value):
        return
    target[key] = value.strip() if isinstance(value, str) else value


def parse_legacy_date(raw):
    """`'DD-MON-YY'` -> aware datetime, or `None` when it is not parseable."""
    if _blank(raw):
        return None
    m = LEGACY_DATE_RE.match(raw.strip().upper())
    if not m:
        return None
    dd, mon, yy = m.group(1), m.group(2), int(m.group(3))
    if mon not in MONTHS:
        return None
    year = 2000 + yy if yy <= YEAR_PIVOT else 1900 + yy
    try:
        return datetime(year, MONTHS[mon], int(dd), tzinfo=timezone.utc)
    except ValueError:
        return None  # e.g. 31-FEB-24, 29-FEB-23


def parse_csv_list(raw, tokens: bool = False):
    """`(values, ok)` for a legacy comma-separated list.

    `ok` is False for anything that is not a clean separated list: empty
    elements, trailing separators, or tokens that are not ids.
    """
    if _blank(raw):
        return [], True
    pattern = CODE_TOKEN_RE if tokens else ID_RE
    values = []
    for part in raw.split(","):
        item = part.strip()
        if not item or not pattern.match(item):
            return [], False
        values.append(item)
    return values, True


def parse_yn(raw):
    if _blank(raw):
        return None
    return {"Y": True, "N": False}.get(raw.strip().upper())


def coerce_attr(value, attr_type):
    """Type an EAV value by its `ATTR_TYPE`; unparseable values stay raw."""
    kind = (attr_type or "STR").strip().upper()
    if value is None:
        return None
    text = value.strip() if isinstance(value, str) else value
    if kind == "NUM":
        try:
            number = float(text)
        except (TypeError, ValueError):
            return text
        return int(number) if number.is_integer() else number
    if kind == "BOOL":
        lowered = str(text).strip().upper()
        if lowered in ("Y", "YES", "TRUE", "T", "1"):
            return True
        if lowered in ("N", "NO", "FALSE", "F", "0"):
            return False
        return text
    if kind == "DATE":
        return parse_legacy_date(text) or text
    return text


def _address(row, kind, line_cols, city_col, state_col, zip_col,
             zip_ext_col=None, country_col=None):
    lines = [str(row[c]).strip() for c in line_cols
             if c in row and not _blank(row.get(c))]
    addr = {"type": kind}
    _put(addr, "lines", lines)
    _put(addr, "city", row.get(city_col))
    _put(addr, "state", row.get(state_col))
    _put(addr, "postalCode", row.get(zip_col))
    if zip_ext_col:
        _put(addr, "postalCodeExt", row.get(zip_ext_col))
    if country_col:
        _put(addr, "country", row.get(country_col))
    return addr if len(addr) > 1 else None


def _phones(row, codes):
    phones = []
    for n in range(1, 5):
        number = row.get(f"PHONE{n}")
        if _blank(number):
            continue
        entry = {}
        code = row.get(f"PHONE{n}_TYPE_CD")
        label = codes.get(("PHONE_TYPE", code)) if code is not None else None
        if label:
            entry["type"] = label
        elif code is not None:
            entry["typeCode"] = code
        entry["number"] = str(number).strip()
        phones.append(entry)
    if not _blank(row.get("FAX")):
        phones.append({"type": "fax", "number": str(row["FAX"]).strip()})
    return phones


def _sparse(row):
    sparse = {}
    for column, value in row.items():
        if column.startswith(SPARSE_PREFIXES) and not _blank(value):
            sparse[column] = value
    return sparse


def _attr_rank(eav):
    """Ordering key for competing EAV rows: newest `CREATED_DT` wins, ties
    broken by the lexicographically greatest `ATTR_VALUE`."""
    created = parse_legacy_date(eav.get("CREATED_DT"))
    return (created or datetime.min.replace(tzinfo=timezone.utc),
            str(eav.get("ATTR_VALUE") or ""))


def _attributes(eav_rows):
    """Fold EAV rows into the name-keyed `attributes` subdocument.

    `attributes` is keyed by `ATTR_NAME`, so rows sharing a name for the same
    customer compete for one slot. The winner is deterministic (see
    `_attr_rank`) and the losers are preserved as `attributeConflicts` entries
    rather than dropped: folded keys + conflict entries always account for
    every source row.
    """
    by_name = {}
    for eav in eav_rows:
        by_name.setdefault(str(eav["ATTR_NAME"]).strip(), []).append(eav)

    attributes, conflicts = {}, []
    for name in sorted(by_name):  # stable field order across reruns
        rows = by_name[name]
        ordered = sorted(rows, key=_attr_rank)
        winner = ordered[-1]
        attributes[name] = coerce_attr(winner.get("ATTR_VALUE"),
                                       winner.get("ATTR_TYPE"))
        for loser in ordered[:-1]:
            entry = {"name": name,
                     "value": coerce_attr(loser.get("ATTR_VALUE"),
                                          loser.get("ATTR_TYPE"))}
            _put(entry, "type", loser.get("ATTR_TYPE"))
            created = loser.get("CREATED_DT")
            _put(entry, "createdAt", parse_legacy_date(created) or created)
            conflicts.append(entry)
    conflicts.sort(key=lambda e: (e["name"], str(e["value"])))
    return attributes, conflicts


def transform_customer(row, eav_rows, codes, ns, migrated_at) -> Transformed:
    """Map a `CUSTOMER_MASTER` row and its EAV rows to a `customers` document.

    `row` maps upper-case column names to values, `eav_rows` are that
    customer's `ENTITY_ATTR_VALUE` rows, and `codes` maps
    `(code_type, code_val)` to the label from the `CODES` table.
    """
    quarantine = []
    doc = {"_id": row["CUST_ID"]}
    _put(doc, "tenantId", row.get("TENANT_ID"))
    _put(doc, "customerNo", row.get("CUST_NO"))

    name = {}
    _put(name, "display", row.get("CUST_NAME"))       # CUST_NAME_UPPER dropped
    _put(name, "legal", row.get("LEGAL_NAME"))
    _put(name, "dba", row.get("DBA_NAME"))
    _put(doc, "name", name)

    addresses = [a for a in (
        _address(row, "primary",
                 [f"ADDR_LINE_{i}" for i in range(1, 7)],
                 "CITY", "STATE_CD", "ZIP", "ZIP4", "COUNTRY_CD"),
        _address(row, "mailing",
                 [f"MAIL_ADDR_LINE_{i}" for i in range(1, 7)],
                 "MAIL_CITY", "MAIL_STATE_CD", "MAIL_ZIP"),
    ) if a]
    _put(doc, "addresses", addresses)
    _put(doc, "phones", _phones(row, codes))
    _put(doc, "emails", [str(row[c]).strip() for c in
                         ("EMAIL_1", "EMAIL_2", "EMAIL_3")
                         if not _blank(row.get(c))])

    status_cd = row.get("STATUS_CD")
    status = codes.get(("CUST_STATUS", status_cd)) if status_cd is not None else None
    if status:
        doc["status"] = status
    elif status_cd is not None:
        doc["statusCode"] = status_cd
    # SUB_STATUS_CD has no 1:1 label in CODES — keep the raw magic number.
    if row.get("SUB_STATUS_CD") is not None:
        doc["subStatusCode"] = row["SUB_STATUS_CD"]
    type_cd = row.get("CUST_TYPE_CD")
    customer_type = codes.get(("CUST_TYPE", type_cd)) if type_cd is not None else None
    if customer_type:
        doc["customerType"] = customer_type
    elif type_cd is not None:
        doc["customerTypeCode"] = type_cd

    classification = {}
    for column, key in CLASSIFICATION_FIELDS:
        if row.get(column) is not None:
            classification[key] = row[column]
    _put(doc, "classification", classification)

    flags = {}
    for column, key in FLAG_FIELDS:
        parsed = parse_yn(row.get(column))
        if parsed is not None:
            flags[key] = parsed
    _put(doc, "flags", flags)

    balances = {}
    for column, key in BALANCE_FIELDS:
        if row.get(column) is not None:
            balances[key] = float(row[column])
    _put(doc, "balances", balances)

    dates = {}
    for column, key in DATE_FIELDS:
        raw = row.get(column)
        if _blank(raw):
            continue
        parsed = parse_legacy_date(raw)
        if parsed is None:
            quarantine.append(Quarantine(column, KIND_DIRTY_DATE, raw))
        else:
            dates[key] = parsed
    _put(doc, "dates", dates)

    for column, key, tokens in CSV_FIELDS:
        raw = row.get(column)
        values, ok = parse_csv_list(raw, tokens=tokens)
        if not ok:
            quarantine.append(Quarantine(column, KIND_MALFORMED_CSV, raw))
            continue
        _put(doc, key, values)

    attributes, attr_conflicts = _attributes(eav_rows)
    _put(doc, "attributes", attributes)

    legacy = {}
    _put(legacy, "sysKey", row.get("LEGACY_SYS_KEY"))
    _put(legacy, "mainframeAcctNo", row.get("MAINFRAME_ACCT_NO"))
    if row.get("CONVERSION_BATCH_NO") is not None:
        legacy["conversionBatchNo"] = row["CONVERSION_BATCH_NO"]
    if row.get("ROW_VERSION_NO") is not None:
        legacy["rowVersionNo"] = row["ROW_VERSION_NO"]
    if row.get("CUST_SEQ_NO") is not None:
        legacy["custSeqNo"] = row["CUST_SEQ_NO"]
    _put(legacy, "contactNotes", row.get("CONTACT_NOTES"))
    audit = {}
    _put(audit, "createdBy", row.get("CREATED_BY"))
    _put(audit, "createdAt", row.get("CREATED_DT"))
    _put(audit, "updatedBy", row.get("UPDATED_BY"))
    _put(audit, "updatedAt", row.get("UPDATED_DT"))
    _put(legacy, "audit", audit)
    _put(legacy, "attributeConflicts", attr_conflicts)
    _put(legacy, "sparse", _sparse(row))
    _put(doc, "legacy", legacy)

    if quarantine:
        doc["_quarantine"] = {q.field: q.raw for q in quarantine}

    doc["_migration"] = {"ns": ns, "sourceTable": SOURCE_TABLE,
                         "migratedAt": migrated_at}
    return Transformed(doc=doc, quarantine=quarantine,
                       eav_rows_consumed=len(eav_rows),
                       attr_keys_folded=len(attributes),
                       attr_conflicts=len(attr_conflicts))
