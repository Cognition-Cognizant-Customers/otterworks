#!/usr/bin/env python3
"""Migrate the batch-scoped Oracle invoice feed into local MongoDB."""
from __future__ import annotations

import argparse
import decimal
import itertools
import os
import sys
from typing import Any

from invoices_model import (
    LINE_COLUMNS,
    SOURCE_ROW_COLUMNS,
    DecodingError,
    NullRequiredField,
    UnparseableRequiredField,
    bulk_replace,
    chunked,
    decimal128,
    decimal128_or_none,
    decimal_value,
    decode_text,
    ensure_collections,
    install_decimal_handler,
    mongo_database,
    oracle_connection,
    parse_legacy_date,
    resolve_batch,
    safe_text,
    source_descriptor,
    validate_namespace,
)
from pymongo import ReplaceOne


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--ns", required=True)
    result.add_argument("--batch-no", type=int)
    result.add_argument("--db", dest="mongo_db", default=os.environ.get("MONGO_DB"))
    result.add_argument("--oracle-host", default=os.environ.get("DB_HOST", "localhost"))
    result.add_argument(
        "--oracle-port", type=int, default=int(os.environ.get("DB_PORT", "52521"))
    )
    result.add_argument(
        "--oracle-user", default=os.environ.get("DB_USER", "ow_billing")
    )
    result.add_argument(
        "--oracle-password", default=os.environ.get("DB_PASSWORD", "ow_billing")
    )
    result.add_argument(
        "--oracle-service", default=os.environ.get("DB_SERVICE", "FREEPDB1")
    )
    return result


def int_value(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def source_row(raw: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in SOURCE_ROW_COLUMNS:
        value = raw[name.lower()]
        if name in {"QTY", "UNIT_PRICE", "AMOUNT", "TAX_AMT"}:
            try:
                amount = decimal_value(value)
            except (decimal.InvalidOperation, TypeError, ValueError):
                values[name] = safe_text(value)
            else:
                values[name] = decimal128_or_none(amount)
        elif name in {"LINE_NO", "LINE_TYPE_CD", "BATCH_NO"}:
            try:
                values[name] = int_value(value)
            except (TypeError, ValueError):
                values[name] = safe_text(value)
        elif name == "POSTED_YN":
            values[name] = safe_text(value)
        else:
            values[name] = safe_text(value)
    return values


def line_key(raw: dict[str, Any]) -> str | None:
    try:
        return decode_text(raw["invoice_id"])
    except DecodingError:
        return safe_text(raw["invoice_id"])


def quarantine_document(
    raw: dict[str, Any],
    namespace: str,
    batch_no: int,
    anomaly_id: str,
    reason: str,
) -> dict[str, Any]:
    invoice_id = safe_text(raw["invoice_id"])
    line_id = safe_text(raw["line_id"])
    if line_id is None:
        raise SystemExit("cannot quarantine source line with NULL LINE_ID")
    try:
        amount = decimal128_or_none(decimal_value(raw["amount"]))
    except (decimal.InvalidOperation, TypeError, ValueError):
        amount = None
    try:
        line_no = int_value(raw["line_no"])
    except (TypeError, ValueError):
        line_no = None
    return {
        "_id": line_id,
        "ns": namespace,
        "anomaly_id": anomaly_id,
        "reason": reason,
        "invoice_id": invoice_id,
        "line_id": line_id,
        "invoice_no": safe_text(raw["invoice_no"]),
        "cust_id": safe_text(raw["cust_id"]),
        "line_no": line_no,
        "amount": amount,
        "source_row": source_row(raw),
        "source": source_descriptor(batch_no),
    }


def normalize_line(raw: dict[str, Any]) -> dict[str, Any]:
    def required_int(value: Any, field: str) -> int:
        if value is None:
            raise NullRequiredField(field)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise UnparseableRequiredField(field) from exc

    def required_decimal(value: Any, field: str) -> decimal.Decimal:
        if value is None:
            raise NullRequiredField(field)
        try:
            result = decimal_value(value)
        except (decimal.InvalidOperation, TypeError, ValueError) as exc:
            raise UnparseableRequiredField(field) from exc
        assert result is not None
        return result

    line_id = decode_text(raw["line_id"])
    if line_id is None:
        raise NullRequiredField("LINE_ID")
    line = {
        "line_id": line_id,
        "invoice_no": decode_text(raw["invoice_no"]),
        "invoice_id": decode_text(raw["invoice_id"]),
        "cust_id": decode_text(raw["cust_id"]),
        "cust_no": decode_text(raw["cust_no"]),
        "cust_name": decode_text(raw["cust_name"]),
        "tenant_id": decode_text(raw["tenant_id"]),
        "line_no": required_int(raw["line_no"], "LINE_NO"),
        "line_type_cd": required_int(raw["line_type_cd"], "LINE_TYPE_CD"),
        "item_desc": decode_text(raw["item_desc"]),
        "qty": required_decimal(raw["qty"], "QTY"),
        "unit_price": required_decimal(raw["unit_price"], "UNIT_PRICE"),
        "amount": decimal_value(raw["amount"]),
        "tax_amt": required_decimal(raw["tax_amt"], "TAX_AMT"),
        "invoice_dt": parse_legacy_date(raw["invoice_dt"]),
        "service_period": decode_text(raw["service_period"]),
        "posted_yn": decode_text(raw["posted_yn"]),
        "gl_acct_csv": decode_text(raw["gl_acct_csv"]),
        "src_system": decode_text(raw["src_system"]),
    }
    if line["amount"] is None:
        raise NullRequiredField("AMOUNT")
    return line


def line_document(line: dict[str, Any]) -> dict[str, Any]:
    invoice_date, invoice_date_raw, _ = line["invoice_dt"]
    posted_value = line["posted_yn"]
    posted = (
        None
        if posted_value is None
        else posted_value.upper() == "Y"
        if posted_value.upper() in {"Y", "N"}
        else None
    )
    gl_csv = line["gl_acct_csv"]
    gl_accts = (
        [] if gl_csv is None else [part.strip() for part in gl_csv.split(",") if part.strip()]
    )
    return {
        "line_id": line["line_id"],
        "line_no": line["line_no"],
        "line_type_cd": line["line_type_cd"],
        "item_desc": line["item_desc"],
        "qty": decimal128(line["qty"]),
        "unit_price": decimal128(line["unit_price"]),
        "amount": decimal128(line["amount"]),
        "tax_amt": decimal128(line["tax_amt"]),
        "invoice_dt": invoice_date,
        "invoice_dt_raw": invoice_date_raw,
        "service_period": line["service_period"],
        "posted": posted,
        "gl_accts": gl_accts,
        "gl_acct_csv": gl_csv,
        "src_system": line["src_system"],
        "cust_no": line["cust_no"],
        "cust_name": line["cust_name"],
    }


def invoice_document(
    header: dict[str, Any],
    lines: list[dict[str, Any]],
    namespace: str,
    batch_no: int,
) -> dict[str, Any]:
    line_documents = sorted(
        (line_document(line) for line in lines),
        key=lambda line: (line["line_no"], line["line_id"]),
    )
    total = sum((line["amount"] for line in lines), decimal.Decimal(0))
    tax = sum((line["tax_amt"] for line in lines), decimal.Decimal(0))
    invoice_date, invoice_date_raw, invoice_date_bad = parse_legacy_date(
        header["invoice_dt"]
    )
    due_date, due_date_raw, due_date_bad = parse_legacy_date(header["due_dt"])
    legacy_total = header["total_amt"]
    if legacy_total is None:
        raise ValueError(f"NULL TOTAL_AMT for invoice {header['invoice_id']}")
    matches = legacy_total == total
    quality: list[str] = []
    if not lines:
        quality.append("no_source_lines")
    if invoice_date_bad is not False:
        quality.append(
            "unparseable_invoice_dt" if invoice_date_bad else "null_invoice_dt"
        )
    if due_date_bad is not False:
        quality.append("unparseable_due_dt" if due_date_bad else "null_due_dt")
    if header.get("duplicate_invoice_no"):
        quality.append("duplicate_invoice_no")
    if not matches:
        quality.append("legacy_total_mismatch")
    return {
        "_id": header["invoice_id"],
        "ns": namespace,
        "invoice_no": header["invoice_no"],
        "cust_id": header["cust_id"],
        "tenant_id": header["tenant_id"],
        "status_cd": header["status_cd"],
        "invoice_dt": invoice_date,
        "due_dt": due_date,
        "invoice_dt_raw": invoice_date_raw,
        "due_dt_raw": due_date_raw,
        "total_amt": decimal128(total),
        "tax_amt": decimal128(tax),
        "legacy_total_amt": decimal128(legacy_total),
        "legacy_total_matches_lines": matches,
        "line_count": len(line_documents),
        "lines": line_documents,
        "source": source_descriptor(batch_no),
        "data_quality": quality,
    }


def fetch_headers(connection: Any, batch_no: int) -> dict[str, dict[str, Any]]:
    cursor = connection.cursor()
    cursor.arraysize = 5000
    install_decimal_handler(cursor)
    cursor.execute(
        """
        SELECT invoice_id, invoice_no, cust_id, tenant_id,
               invoice_dt, due_dt, status_cd, total_amt
          FROM OW_BILLING.invoice_header
         WHERE batch_no = :batch_no
        """,
        batch_no=batch_no,
    )
    headers: dict[str, dict[str, Any]] = {}
    invoice_no_counts: dict[str, int] = {}
    for row in cursor:
        values = dict(zip(
            (
                "invoice_id",
                "invoice_no",
                "cust_id",
                "tenant_id",
                "invoice_dt",
                "due_dt",
                "status_cd",
                "total_amt",
            ),
            row,
        ))
        header = {
            "invoice_id": decode_text(values["invoice_id"]),
            "invoice_no": decode_text(values["invoice_no"]),
            "cust_id": decode_text(values["cust_id"]),
            "tenant_id": decode_text(values["tenant_id"]),
            "invoice_dt": values["invoice_dt"],
            "due_dt": values["due_dt"],
        }
        if header["invoice_id"] is None:
            raise SystemExit(
                f"NULL INVOICE_ID in invoice_header batch {batch_no}"
            )
        if values["status_cd"] is None:
            raise SystemExit(
                f"NULL STATUS_CD for invoice {header['invoice_id']} "
                f"in batch {batch_no}"
            )
        try:
            header["status_cd"] = int(values["status_cd"])
        except (TypeError, ValueError) as exc:
            raise SystemExit(
                f"unparseable STATUS_CD for invoice {header['invoice_id']} "
                f"in batch {batch_no}"
            ) from exc
        if values["total_amt"] is None:
            raise SystemExit(
                f"NULL TOTAL_AMT for invoice {header['invoice_id']} "
                f"in batch {batch_no}"
            )
        try:
            header["total_amt"] = decimal_value(values["total_amt"])
        except (decimal.InvalidOperation, TypeError, ValueError) as exc:
            raise SystemExit(
                f"unparseable TOTAL_AMT for invoice {header['invoice_id']} "
                f"in batch {batch_no}"
            ) from exc
        headers[header["invoice_id"]] = header
        invoice_no = header["invoice_no"]
        if invoice_no is not None:
            invoice_no_counts[invoice_no] = invoice_no_counts.get(invoice_no, 0) + 1
    cursor.close()
    for header in headers.values():
        invoice_no = header["invoice_no"]
        header["duplicate_invoice_no"] = (
            invoice_no is not None and invoice_no_counts[invoice_no] > 1
        )
    return headers


def line_cursor(connection: Any, batch_no: int) -> Any:
    cursor = connection.cursor()
    cursor.arraysize = 5000
    install_decimal_handler(cursor)
    cursor.execute(
        """
        SELECT line_id, invoice_no, invoice_id, cust_id, cust_no, cust_name,
               tenant_id, line_no, line_type_cd, item_desc, qty, unit_price,
               amount, tax_amt, invoice_dt, service_period, posted_yn,
               gl_acct_csv, batch_no, src_system
          FROM OW_BILLING.invoice_line
         WHERE batch_no = :batch_no
         ORDER BY invoice_id, line_id
        """,
        batch_no=batch_no,
    )
    return cursor


def raw_line(row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(LINE_COLUMNS, row))


def write_stale_cleanup(
    invoices: Any,
    quarantine: Any,
    namespace: str,
    batch_no: int,
    invoice_ids: set[str],
    quarantine_ids: set[str],
) -> None:
    invoice_scope = {"ns": namespace, "source.batch_no": batch_no}
    existing_invoice_ids = {
        value["_id"] for value in invoices.find(invoice_scope, {"_id": 1})
    }
    stale_invoice_ids = existing_invoice_ids - invoice_ids
    for values in chunked(stale_invoice_ids, 1000):
        if values:
            invoices.delete_many(
                {"_id": {"$in": values}, "ns": namespace, "source.batch_no": batch_no}
            )
    quarantine_scope = {"ns": namespace, "source.batch_no": batch_no}
    existing_quarantine_ids = {
        value["_id"] for value in quarantine.find(quarantine_scope, {"_id": 1})
    }
    stale_quarantine_ids = existing_quarantine_ids - quarantine_ids
    for values in chunked(stale_quarantine_ids, 1000):
        if values:
            quarantine.delete_many(
                {"_id": {"$in": values}, "ns": namespace, "source.batch_no": batch_no}
            )


def migrate(args: argparse.Namespace) -> None:
    validate_namespace(args.ns)
    batch_no = resolve_batch(args.ns, args.batch_no)
    connection = oracle_connection(args)
    try:
        headers = fetch_headers(connection, batch_no)
        cursor = line_cursor(connection, batch_no)
        first_row = cursor.fetchone()
        if not headers and first_row is None:
            raise SystemExit(
                f"empty batch input: no invoice_header or invoice_line rows for batch {batch_no}"
            )

        mongo_client, database = mongo_database(args)
        try:
            ensure_collections(database)
            invoices = database["invoices"]
            quarantine = database["invoices_quarantine"]
            invoice_operations: list[ReplaceOne] = []
            quarantine_operations: list[ReplaceOne] = []
            invoice_ids: set[str] = set()
            quarantine_ids: set[str] = set()
            written_headers: set[str] = set()
            current_invoice_id: str | None = None
            current_lines: list[dict[str, Any]] = []
            current_set = False

            def flush_invoice() -> None:
                nonlocal current_invoice_id, current_lines, current_set
                if not current_set:
                    return
                if current_invoice_id in headers:
                    invoice = invoice_document(
                        headers[current_invoice_id],
                        current_lines,
                        args.ns,
                        batch_no,
                    )
                    invoice_operations.append(
                        ReplaceOne({"_id": invoice["_id"]}, invoice, upsert=True)
                    )
                    invoice_ids.add(invoice["_id"])
                    written_headers.add(invoice["_id"])
                    if len(invoice_operations) >= 1000:
                        bulk_replace(invoices, invoice_operations)
                current_invoice_id = None
                current_lines = []
                current_set = False

            rows = itertools.chain(
                () if first_row is None else (first_row,),
                cursor,
            )
            for row in rows:
                raw = raw_line(row)
                key = line_key(raw)
                if not current_set:
                    current_invoice_id = key
                    current_set = True
                elif key != current_invoice_id:
                    flush_invoice()
                    current_invoice_id = key
                    current_set = True
                try:
                    line = normalize_line(raw)
                except DecodingError:
                    document = quarantine_document(
                        raw,
                        args.ns,
                        batch_no,
                        "invalid_encoding",
                        "a source string could not be decoded as UTF-8",
                    )
                except decimal.InvalidOperation:
                    document = quarantine_document(
                        raw,
                        args.ns,
                        batch_no,
                        "unparseable_amount",
                        "AMOUNT cannot be parsed as Decimal",
                    )
                except NullRequiredField as exc:
                    if exc.field == "LINE_ID":
                        raise SystemExit(
                            f"source line has NULL LINE_ID in batch {batch_no}; "
                            "LINE_ID is required as the source primary key"
                        ) from exc
                    anomaly = "null_amount" if exc.field == "AMOUNT" else "null_required_field"
                    document = quarantine_document(
                        raw,
                        args.ns,
                        batch_no,
                        anomaly,
                        f"{exc.field} is NULL",
                    )
                except UnparseableRequiredField as exc:
                    document = quarantine_document(
                        raw,
                        args.ns,
                        batch_no,
                        "null_required_field",
                        f"{exc.field} is unparseable",
                    )
                else:
                    if line["invoice_id"] is None:
                        document = quarantine_document(
                            raw,
                            args.ns,
                            batch_no,
                            "null_invoice_id",
                            "INVOICE_ID is NULL",
                        )
                    elif line["invoice_id"] not in headers:
                        document = quarantine_document(
                            raw,
                            args.ns,
                            batch_no,
                            "orphaned_rows",
                            "parent INVOICE_HEADER is missing",
                        )
                    else:
                        current_lines.append(line)
                        continue
                quarantine_operations.append(
                    ReplaceOne({"_id": document["_id"]}, document, upsert=True)
                )
                quarantine_ids.add(document["_id"])
                if len(quarantine_operations) >= 1000:
                    bulk_replace(quarantine, quarantine_operations)

            flush_invoice()
            for invoice_id, header in headers.items():
                if invoice_id not in written_headers:
                    document = invoice_document(
                        header, [], args.ns, batch_no
                    )
                    invoice_operations.append(
                        ReplaceOne({"_id": document["_id"]}, document, upsert=True)
                    )
                    invoice_ids.add(document["_id"])
                    if len(invoice_operations) >= 1000:
                        bulk_replace(invoices, invoice_operations)

            bulk_replace(invoices, invoice_operations)
            bulk_replace(quarantine, quarantine_operations)
            write_stale_cleanup(
                invoices,
                quarantine,
                args.ns,
                batch_no,
                invoice_ids,
                quarantine_ids,
            )
        finally:
            mongo_client.close()
    finally:
        connection.close()


def main() -> int:
    args = parser().parse_args()
    batch_no = resolve_batch(args.ns, args.batch_no)
    migrate(args)
    print(f"migrated namespace={args.ns} batch={batch_no}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
