"""CUSTBILL fixed-width parsing + finance-report aggregation.

Semantically equivalent to the legacy chain in etl/legacy-extra/jobs/:
  - parse_custbill_fixedwidth.sh (copybook CBCUST01 layout)
  - finance_excel_report.pl (totals by currency + record type)

Layout (copybook CBCUST01):
  pos  1-10   CUST-ID    PIC X(10)
  pos 11-40   CUST-NAME  PIC X(30)
  pos 41-48   BILL-DATE  PIC 9(8)  YYYYMMDD
  pos 49-60   BILL-AMT   PIC 9(10)V99 (implied decimal)
  pos 61-63   CURRENCY   PIC X(3)
  pos 64-65   REC-TYPE   PIC X(2)  (01=invoice 02=credit)
"""

import re

_NUM_PREFIX = re.compile(r"^\s*[-+]?\d*\.?\d*")


def _awk_num(s: str) -> float:
    """awk's `$4+0`: numeric value of the leading number, else 0."""
    m = _NUM_PREFIX.match(s)
    tok = m.group(0).strip() if m else ""
    try:
        return float(tok)
    except ValueError:
        return 0.0


def parse_line(line: str) -> str:
    """One fixed-width body record -> one pipe-delimited record."""
    cust = line[0:10].rstrip(" ")
    name = line[10:40].rstrip(" ")
    date = line[40:48]
    amt = line[48:60]
    ccy = line[60:63].rstrip(" ")
    rt = line[63:65]
    iso = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
    amount = f"{_awk_num(amt) / 100:.2f}"
    return "|".join([cust, name, iso, amount, ccy, rt])


def parse_file(text: str) -> list[str]:
    """Full CUSTBILL file -> list of pipe-delimited records (HDR/TRL stripped)."""
    out = []
    # split on \n only (like sed/cut); splitlines() would also break on
    # control bytes such as \x0b or NEL that the legacy chain keeps in-record
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for line in lines:
        if line.startswith("HDR") or line.startswith("TRL"):
            continue
        out.append(parse_line(line))
    return out


def finance_report(psv_lines: list[str]) -> str:
    """Aggregate parsed records into the finance CSV (header + sorted totals)."""
    tot: dict[str, float] = {}
    cnt: dict[str, int] = {}
    for line in psv_lines:
        fields = line.split("|")
        if not fields or fields[0] == "":
            continue
        cust, name, dt, amt, ccy, rt = (fields + [""] * 6)[:6]
        key = f"{ccy}|{rt}"
        tot[key] = tot.get(key, 0.0) + _awk_num(amt)
        cnt[key] = cnt.get(key, 0) + 1

    rows = ["Currency,RecordType,RecordCount,TotalAmount"]
    for key in sorted(tot.keys()):
        ccy, _, rt = key.partition("|")
        rtname = "INVOICE" if rt == "01" else "CREDIT" if rt == "02" else f"UNKNOWN({rt})"
        rows.append(f"{ccy},{rtname},{cnt[key]},{tot[key]:.2f}")
    return "\n".join(rows) + "\n"
