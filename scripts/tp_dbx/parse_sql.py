"""Namespace-scoped names and SQL for the dbx-parse unit.

One place for every statement so the SQL the job runs and the SQL the recon
harness runs are provably the same text, parameterised by namespace and named
write parameters. Every object carries the `ow_tp` prefix and the namespace
suffix: the demo workspace is shared, so nothing here can touch another
namespace's slice.

Spark-free and dependency-free — this module is inlined verbatim into the
Databricks notebook task and imported by the local fixture harness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

UNIT = "parse"
NS_RE = re.compile(r"[a-z0-9_]{1,24}")
IDENT_RE = re.compile(r"[A-Za-z0-9_]+")


def require_ns(ns: str) -> str:
    if not NS_RE.fullmatch(ns):
        raise SystemExit(f"namespace must match [a-z0-9_]{{1,24}}: {ns!r}")
    return ns


def require_ident(value: str, label: str) -> str:
    if not IDENT_RE.fullmatch(value):
        raise SystemExit(f"{label} must match [A-Za-z0-9_]+: {value!r}")
    return value


def esc(value: str) -> str:
    """Databricks string literals honour backslash escapes, so a value ending in a
    backslash would otherwise neutralise the closing quote."""
    return value.replace("\\", "\\\\").replace("'", "''")


@dataclass(frozen=True)
class Names:
    catalog: str = "ow_tp"
    ns: str = "demo"

    def __post_init__(self):
        require_ident(self.catalog, "catalog")
        require_ns(self.ns)

    @property
    def landing(self) -> str:
        return f"/Volumes/{self.catalog}/bronze/landing/{self.ns}/{UNIT}"

    @property
    def incoming(self) -> str:
        return f"{self.landing}/incoming"

    @property
    def silver(self) -> str:
        return f"{self.catalog}.silver.custbill_records_{self.ns}"

    @property
    def quarantine(self) -> str:
        return f"{self.catalog}.silver.custbill_quarantine_{self.ns}"

    @property
    def job(self) -> str:
        return f"ow_tp_{UNIT}_{self.ns}"

    @property
    def notebook(self) -> str:
        return f"/Shared/ow_tp/{UNIT}_custbill_{self.ns}"


def ddl(n: Names) -> list:
    """Only this namespace's own tables; the catalog, schemas and volume are
    parent-owned and never created or altered here."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {n.silver} (
              ns STRING COMMENT 'Demo namespace this row belongs to',
              source_file STRING COMMENT 'CUSTBILL extract file the record came from',
              source_line INT COMMENT 'Line number within the source file',
              cust_id STRING COMMENT 'CBCUST01 pos 1-10',
              cust_name STRING COMMENT 'CBCUST01 pos 11-40',
              bill_date DATE COMMENT 'CBCUST01 pos 41-48, validated calendar date',
              amount_cents BIGINT COMMENT 'CBCUST01 pos 49-60 PIC 9(10)V99 implied decimal held exactly as cents',
              currency STRING COMMENT 'CBCUST01 pos 61-63',
              record_type STRING COMMENT 'CBCUST01 pos 64-65: 01=invoice 02=credit')
            USING DELTA
            COMMENT 'Silver: schema-validated CUSTBILL records, one row per accepted fixed-width body line'""",
        f"""CREATE TABLE IF NOT EXISTS {n.quarantine} (
              ns STRING COMMENT 'Demo namespace this row belongs to',
              source_file STRING COMMENT 'CUSTBILL extract file the rejected line came from',
              source_line INT COMMENT 'Line number within the source file (0 for a whole-file rejection)',
              raw_bytes_base64 STRING COMMENT 'Exact source bytes of the line, base64 encoded for byte transparency',
              raw_line STRING COMMENT 'Same bytes rendered with a lossless single-byte decode, for reading',
              reason_code STRING COMMENT 'Why the record was rejected',
              detail STRING COMMENT 'Human-readable specifics, e.g. both trailer and record counts')
            USING DELTA
            COMMENT 'Silver quarantine: every rejected CUSTBILL line with its raw bytes and a reason code'""",
    ]


def delete_file_rows(n: Names, source_file: str) -> list:
    """Per-file replace: makes a rerun idempotent without touching other files."""
    return [
        (
            f"DELETE FROM {n.silver} WHERE ns = :ns AND source_file = :source_file",
            {"ns": n.ns, "source_file": source_file},
        ),
        (
            f"DELETE FROM {n.quarantine} WHERE ns = :ns AND source_file = :source_file",
            {"ns": n.ns, "source_file": source_file},
        ),
    ]


def insert_records(n: Names, records: list) -> list:
    statements = []
    for chunk in _chunks(records, 200):
        values = []
        params = {"ns": n.ns}
        for i, r in enumerate(chunk):
            prefix = f"r{i}_"
            values.append(
                f"(:ns, :{prefix}source_file, :{prefix}source_line, :{prefix}cust_id, "
                f":{prefix}cust_name, CAST(:{prefix}bill_date AS DATE), "
                f":{prefix}amount_cents, :{prefix}currency, :{prefix}record_type)"
            )
            params.update({
                f"{prefix}source_file": r.source_file,
                f"{prefix}source_line": int(r.source_line),
                f"{prefix}cust_id": r.cust_id,
                f"{prefix}cust_name": r.cust_name,
                f"{prefix}bill_date": r.bill_date,
                f"{prefix}amount_cents": int(r.amount_cents),
                f"{prefix}currency": r.currency,
                f"{prefix}record_type": r.record_type,
            })
        statements.append(
            (
                (
                    f"INSERT INTO {n.silver} (ns, source_file, source_line, cust_id, cust_name, "
                    f"bill_date, amount_cents, currency, record_type) VALUES {', '.join(values)}"
                ),
                params,
            )
        )
    return statements


def insert_rejects(n: Names, rejects: list) -> list:
    statements = []
    for chunk in _chunks(rejects, 200):
        values = []
        params = {"ns": n.ns}
        for i, q in enumerate(chunk):
            prefix = f"r{i}_"
            values.append(
                f"(:ns, :{prefix}source_file, :{prefix}source_line, "
                f":{prefix}raw_bytes_base64, :{prefix}raw_line, :{prefix}reason_code, "
                f":{prefix}detail)"
            )
            params.update({
                f"{prefix}source_file": q.source_file,
                f"{prefix}source_line": int(q.source_line),
                f"{prefix}raw_bytes_base64": q.raw_bytes_base64,
                f"{prefix}raw_line": q.raw_line,
                f"{prefix}reason_code": q.reason_code,
                f"{prefix}detail": q.detail,
            })
        statements.append(
            (
                (
                    f"INSERT INTO {n.quarantine} (ns, source_file, source_line, raw_bytes_base64, "
                    f"raw_line, reason_code, detail) VALUES {', '.join(values)}"
                ),
                params,
            )
        )
    return statements


def _chunks(items: list, size: int) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


def silver_rows(n: Names) -> str:
    return (f"SELECT source_file, source_line, cust_id, cust_name, CAST(bill_date AS STRING), "
            f"amount_cents, currency, record_type FROM {n.silver} WHERE ns = '{esc(n.ns)}' "
            "ORDER BY source_file, source_line")


def silver_totals(n: Names) -> str:
    """Finance-shaped rollup recomputed from the target, for the to-the-cent
    comparison against the legacy .psv."""
    return (f"SELECT currency, CASE record_type WHEN '01' THEN 'INVOICE' ELSE 'CREDIT' END AS kind, "
            f"COUNT(*) AS n, SUM(amount_cents) AS cents FROM {n.silver} WHERE ns = '{esc(n.ns)}' "
            "GROUP BY currency, record_type ORDER BY currency, record_type")


def quarantine_rows(n: Names) -> str:
    return (f"SELECT source_file, source_line, reason_code, detail, raw_bytes_base64 "
            f"FROM {n.quarantine} WHERE ns = '{esc(n.ns)}' ORDER BY source_file, source_line, reason_code")


def counts(n: Names) -> str:
    return (f"SELECT (SELECT COUNT(*) FROM {n.silver} WHERE ns = '{esc(n.ns)}') AS silver_rows, "
            f"(SELECT COUNT(*) FROM {n.quarantine} WHERE ns = '{esc(n.ns)}') AS quarantine_rows")
