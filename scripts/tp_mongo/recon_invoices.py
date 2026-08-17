#!/usr/bin/env python3
"""Run the Mongo invoice migration twice and emit a target-side recon report."""
from __future__ import annotations

import argparse
import decimal
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import Decimal128
from bson.json_util import CANONICAL_JSON_OPTIONS
from bson.json_util import dumps as bson_dumps
from invoices_model import (
    decimal_text,
    install_decimal_handler,
    load_manifest,
    mongo_database,
    oracle_connection,
    resolve_batch,
    source_descriptor,
    validate_namespace,
)
from pymongo.errors import OperationFailure, WriteError

RECON_DEFAULT = "docs/tech-partnerships/recon/mongo_invoices.recon.json"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--ns", required=True)
    result.add_argument("--batch-no", type=int)
    result.add_argument("--db", dest="mongo_db", default=os.environ.get("MONGO_DB"))
    result.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    result.add_argument("--out", default=RECON_DEFAULT)
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


def run_migration(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        str(Path(__file__).with_name("migrate_invoices.py")),
        "--ns",
        args.ns,
    ]
    if args.batch_no is not None:
        command.extend(["--batch-no", str(args.batch_no)])
    if args.mongo_db:
        command.extend(["--db", args.mongo_db])
    command.extend(
        [
            "--oracle-host",
            args.oracle_host,
            "--oracle-port",
            str(args.oracle_port),
            "--oracle-user",
            args.oracle_user,
            "--oracle-service",
            args.oracle_service,
        ]
    )
    child_env = os.environ.copy()
    child_env["DB_PASSWORD"] = args.oracle_password
    subprocess.run(command, check=True, env=child_env)


def source_facts(
    args: argparse.Namespace, batch_no: int
) -> tuple[
    set[str],
    dict[str, decimal.Decimal],
    set[str],
    int,
    int,
    int,
    str,
]:
    connection = oracle_connection(args)
    try:
        cursor = connection.cursor()
        cursor.arraysize = 5000
        install_decimal_handler(cursor)
        cursor.execute(
            """
            SELECT invoice_id, total_amt
              FROM OW_BILLING.invoice_header
             WHERE batch_no = :batch_no
            """,
            batch_no=batch_no,
        )
        header_totals: dict[str, decimal.Decimal] = {}
        for invoice_id, total_amt in cursor:
            if invoice_id is not None:
                header_totals[str(invoice_id)] = total_amt
        cursor.close()

        cursor = connection.cursor()
        cursor.arraysize = 5000
        install_decimal_handler(cursor)
        cursor.execute(
            """
            SELECT line_id, invoice_id, amount, posted_yn
              FROM OW_BILLING.invoice_line
             WHERE batch_no = :batch_no
            """,
            batch_no=batch_no,
        )
        line_totals: dict[str, decimal.Decimal] = {}
        non_orphan_lines: list[tuple[str, decimal.Decimal]] = []
        for line_id, invoice_id, amount, _posted in cursor:
            invoice_key = str(invoice_id)
            if invoice_key in header_totals:
                line_totals[invoice_key] = (
                    line_totals.get(invoice_key, decimal.Decimal(0)) + amount
                )
                non_orphan_lines.append((str(line_id), amount))
        cursor.close()

        cursor = connection.cursor()
        cursor.arraysize = 5000
        install_decimal_handler(cursor)
        cursor.execute(
            """
            SELECT l.line_id
              FROM OW_BILLING.invoice_line l
              LEFT JOIN OW_BILLING.invoice_header h
                ON h.invoice_id = l.invoice_id
               AND h.batch_no = :batch_no
             WHERE l.batch_no = :batch_no
               AND h.invoice_id IS NULL
            """,
            batch_no=batch_no,
        )
        orphan_ids = {str(line_id) for (line_id,) in cursor}
        cursor.close()

        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
              FROM OW_BILLING.invoice_line l
              JOIN OW_BILLING.invoice_header h
                ON h.invoice_id = l.invoice_id
               AND h.batch_no = :batch_no
             WHERE l.batch_no = :batch_no
               AND l.posted_yn IS NULL
            """,
            batch_no=batch_no,
        )
        null_posted = int(cursor.fetchone()[0])
        cursor.close()
        zero_line = sum(
            1 for invoice_id in header_totals if invoice_id not in line_totals
        )
        mismatch = sum(
            1
            for invoice_id, total in header_totals.items()
            if total != line_totals.get(invoice_id, decimal.Decimal(0))
        )
        return (
            set(header_totals),
            header_totals,
            orphan_ids,
            null_posted,
            zero_line,
            mismatch,
            checksum(non_orphan_lines),
        )
    finally:
        connection.close()


def target_lines(
    invoices: Any, quarantine: Any, namespace: str
) -> tuple[list[tuple[str, Any]], list[tuple[str, Any]], set[str]]:
    embedded: list[tuple[str, Any]] = []
    embedded_ids: set[str] = set()
    for document in invoices.find(
        {"ns": namespace}, {"lines.line_id": 1, "lines.amount": 1}
    ):
        for line in document.get("lines", []):
            embedded.append((line["line_id"], line["amount"]))
            embedded_ids.add(line["line_id"])
    quarantined = [
        (document["_id"], document.get("amount"))
        for document in quarantine.find(
            {"ns": namespace}, {"_id": 1, "amount": 1}
        )
    ]
    return embedded, quarantined, embedded_ids


def checksum(lines: list[tuple[str, Any]]) -> str:
    digest = hashlib.md5()
    for line_id, amount in sorted(lines, key=lambda value: value[0]):
        amount_text = "" if amount is None else decimal_text(amount)
        digest.update(f"{line_id}:{amount_text}\n".encode())
    return digest.hexdigest()


def quarantine_hash(quarantine: Any, namespace: str) -> str:
    ids = sorted(
        document["_id"]
        for document in quarantine.find({"ns": namespace}, {"_id": 1})
    )
    return hashlib.md5("\n".join(ids).encode()).hexdigest()


def embedded_line_count(invoices: Any, namespace: str) -> int:
    rows = list(
        invoices.aggregate(
            [
                {"$match": {"ns": namespace}},
                {"$group": {"_id": None, "count": {"$sum": {"$size": "$lines"}}}},
            ]
        )
    )
    return int(rows[0]["count"]) if rows else 0


def fingerprint(invoices: Any, quarantine: Any, namespace: str) -> dict[str, Any]:
    embedded, quarantined, _ = target_lines(invoices, quarantine, namespace)
    content_digest = hashlib.md5()
    for collection_name, collection in (
        ("invoices", invoices),
        ("invoices_quarantine", quarantine),
    ):
        for document in collection.find({"ns": namespace}).sort("_id", 1):
            content_digest.update(collection_name.encode())
            content_digest.update(b"\0")
            content_digest.update(
                bson_dumps(
                    document,
                    json_options=CANONICAL_JSON_OPTIONS,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
            content_digest.update(b"\n")
    return {
        "invoice_doc_count": invoices.count_documents({"ns": namespace}),
        "total_embedded_line_count": embedded_line_count(invoices, namespace),
        "all_lines_checksum": checksum(embedded + quarantined),
        "quarantine_count": quarantine.count_documents({"ns": namespace}),
        "sorted_quarantine_id_hash": quarantine_hash(quarantine, namespace),
        "full_documents_hash": content_digest.hexdigest(),
    }


def aggregation_count(collection: Any, pipeline: list[dict[str, Any]]) -> int:
    rows = list(collection.aggregate(pipeline))
    return int(rows[0]["count"]) if rows else 0


def validator_names(database: Any) -> list[str]:
    names: list[str] = []
    for info in database.list_collections(
        filter={"name": {"$in": ["invoices", "invoices_quarantine"]}}
    ):
        validator = info.get("options", {}).get("validator", {})
        if isinstance(validator, dict) and "$jsonSchema" in validator:
            names.append(info["name"])
    return sorted(names)


def validator_probe(invoices: Any, namespace: str, batch_no: int) -> tuple[str, bool]:
    probe_id = "ow_tp_validator_probe"
    invoices.delete_one({"_id": probe_id})
    probe = {
        "_id": probe_id,
        "ns": namespace,
        "invoice_no": "validator-probe",
        "cust_id": "validator-probe",
        "tenant_id": "validator-probe",
        "status_cd": 1,
        "invoice_dt": None,
        "due_dt": None,
        "invoice_dt_raw": None,
        "due_dt_raw": None,
        "total_amt": 1.0,
        "tax_amt": Decimal128(decimal.Decimal(0)),
        "legacy_total_amt": Decimal128(decimal.Decimal(0)),
        "legacy_total_matches_lines": False,
        "line_count": 0,
        "lines": [],
        "source": source_descriptor(batch_no),
        "data_quality": [],
    }
    result = "accepted"
    code: int | None = None
    try:
        invoices.insert_one(probe)
    except (WriteError, OperationFailure) as exc:
        code = getattr(exc, "code", None)
        result = f"rejected_code_{code}" if code is not None else "rejected"
    if invoices.find_one({"_id": probe_id}) is not None:
        invoices.delete_one({"_id": probe_id})
    absent = invoices.find_one({"_id": probe_id}) is None
    return result, absent and code == 121


def make_check(
    check_id: str, expected: Any, actual: Any, source: str, passed: bool | None = None
) -> dict[str, Any]:
    if passed is None:
        passed = expected == actual
    return {
        "id": check_id,
        "expected": expected,
        "actual": actual,
        "source_of_truth": source,
        "result": "pass" if passed else "fail",
    }


def report(
    args: argparse.Namespace, batch_no: int
) -> tuple[dict[str, Any], bool]:
    manifest = load_manifest(args.ns)
    line_target = manifest["targets"]["oracle.OW_BILLING.INVOICE_LINE"]
    header_target = manifest["targets"]["oracle.OW_BILLING.INVOICE_HEADER"]
    line_anomalies = [
        item
        for item in manifest["planted_anomalies"]
        if item.get("target") == "oracle.OW_BILLING.INVOICE_LINE"
    ]
    expected_anomaly_set = sorted({item["kind"] for item in line_anomalies})
    orphan_count = next(
        item["count"]
        for item in line_anomalies
        if item["kind"] == "orphaned_rows"
    )
    (
        source_header_ids,
        _source_header_totals,
        source_orphan_ids,
        source_null_posted,
        source_zero_line,
        source_mismatch,
        source_non_orphan_checksum,
    ) = source_facts(args, batch_no)
    mongo_client, database = mongo_database(args)
    try:
        invoices = database["invoices"]
        quarantine = database["invoices_quarantine"]
        embedded, quarantined, embedded_ids = target_lines(
            invoices, quarantine, args.ns
        )
        embedded_docs = list(invoices.find({"ns": args.ns}))
        embedded_line_count_value = embedded_line_count(invoices, args.ns)
        line_total_aggregation = aggregation_count(
            invoices,
            [
                {"$match": {"ns": args.ns}},
                {
                    "$project": {
                        "mismatch": {
                            "$ne": ["$total_amt", {"$sum": "$lines.amount"}]
                        }
                    }
                },
                {"$match": {"mismatch": True}},
                {"$count": "count"},
            ],
        )
        line_total_python = sum(
            1
            for document in embedded_docs
            if document["total_amt"].to_decimal()
            != sum(
                (line["amount"].to_decimal() for line in document["lines"]),
                decimal.Decimal(0),
            )
        )
        money_pipeline = [
            {"$match": {"ns": args.ns}},
            {
                "$match": {
                    "$expr": {
                        "$or": [
                            {"$ne": [{"$type": "$total_amt"}, "decimal"]},
                            {"$ne": [{"$type": "$tax_amt"}, "decimal"]},
                            {"$ne": [{"$type": "$legacy_total_amt"}, "decimal"]},
                            {
                                "$gt": [
                                    {
                                        "$size": {
                                            "$filter": {
                                                "input": "$lines",
                                                "as": "line",
                                                "cond": {
                                                    "$ne": [
                                                        {"$type": "$$line.amount"},
                                                        "decimal",
                                                    ]
                                                },
                                            }
                                        }
                                    },
                                    0,
                                ]
                            },
                            {
                                "$gt": [
                                    {
                                        "$size": {
                                            "$filter": {
                                                "input": "$lines",
                                                "as": "line",
                                                "cond": {
                                                    "$ne": [
                                                        {"$type": "$$line.tax_amt"},
                                                        "decimal",
                                                    ]
                                                },
                                            }
                                        }
                                    },
                                    0,
                                ]
                            },
                            {
                                "$gt": [
                                    {
                                        "$size": {
                                            "$filter": {
                                                "input": "$lines",
                                                "as": "line",
                                                "cond": {
                                                    "$ne": [
                                                        {"$type": "$$line.qty"},
                                                        "decimal",
                                                    ]
                                                },
                                            }
                                        }
                                    },
                                    0,
                                ]
                            },
                            {
                                "$gt": [
                                    {
                                        "$size": {
                                            "$filter": {
                                                "input": "$lines",
                                                "as": "line",
                                                "cond": {
                                                    "$ne": [
                                                        {"$type": "$$line.unit_price"},
                                                        "decimal",
                                                    ]
                                                },
                                            }
                                        }
                                    },
                                    0,
                                ]
                            },
                        ]
                    }
                },
            },
            {"$count": "count"},
        ]
        money_not_decimal = aggregation_count(invoices, money_pipeline)
        target_invoice_count = invoices.count_documents({"ns": args.ns})
        target_orphan_count = quarantine.count_documents(
            {"ns": args.ns, "anomaly_id": "orphaned_rows"}
        )
        target_zero_line_count = invoices.count_documents(
            {"ns": args.ns, "line_count": 0}
        )
        target_orphan_ids = {
            document["_id"]
            for document in quarantine.find(
                {"ns": args.ns, "anomaly_id": "orphaned_rows"}, {"_id": 1}
            )
        }
        target_null_posted = sum(
            1
            for document in embedded_docs
            for line in document["lines"]
            if line["posted"] is None and line["posted_raw"] is None
        )
        target_mismatch = sum(
            1
            for document in embedded_docs
            if not document["legacy_total_matches_lines"]
        )
        target_ids = {document["_id"] for document in embedded_docs}
        validator_actual = validator_names(database)
        probe_actual, probe_passed = validator_probe(
            invoices, args.ns, batch_no
        )
        all_anomaly_ids = sorted(
            {
                document["anomaly_id"]
                for document in quarantine.find(
                    {"ns": args.ns}, {"anomaly_id": 1}
                )
            }
        )
    finally:
        mongo_client.close()

    checks = [
        make_check(
            "invoices.count",
            header_target["rows"],
            target_invoice_count,
            "baseline manifest targets.oracle.OW_BILLING.INVOICE_HEADER.rows; target count_documents({ns})",
            target_invoice_count == header_target["rows"],
        ),
        make_check(
            "invoices.embedded_line_count",
            line_target["rows"] - orphan_count,
            embedded_line_count_value,
            "baseline manifest INVOICE_LINE.rows minus planted anomaly count; target aggregation over lines",
        ),
        make_check(
            "invoices.checksum",
            line_target["checksum"],
            checksum(embedded + quarantined),
            "manifest digest spans all 150000 source rows, including quarantined orphans; target read-back",
        ),
        make_check(
            "invoices.checksum_non_orphan_lines",
            source_non_orphan_checksum,
            checksum(embedded),
            "Oracle batch recomputation over header-backed lines; target read-back over embedded non-orphan lines",
        ),
        make_check(
            "invoices.line_totals_preserved",
            0,
            line_total_aggregation,
            "target aggregation comparing total_amt with Decimal128 sum of embedded line amounts; Python read-back mismatch count="
            f"{line_total_python}",
            line_total_aggregation == 0 and line_total_python == 0,
        ),
        make_check(
            "invoices.money_is_decimal128",
            0,
            money_not_decimal,
            "target $type aggregation over invoice and embedded-line money fields",
        ),
        make_check(
            "invoices.orphans_quarantined",
            orphan_count,
            target_orphan_count,
            "baseline manifest planted anomaly orphaned_rows.count; target count_documents",
        ),
        make_check(
            "invoices.orphan_line_id_set_equality",
            0,
            len(source_orphan_ids.symmetric_difference(target_orphan_ids)),
            "Oracle LEFT JOIN recomputation of orphan line_id set versus target quarantine",
        ),
        make_check(
            "invoices.orphans_not_embedded",
            0,
            len(source_orphan_ids & embedded_ids),
            "target read-back intersection of Oracle orphan IDs and embedded line IDs",
        ),
        make_check(
            "invoices.no_synthesized_headers",
            0,
            len(target_ids - source_header_ids),
            "Oracle batch header ID set versus target invoice IDs",
        ),
        make_check(
            "invoices.zero_line_invoices",
            source_zero_line,
            target_zero_line_count,
            "Oracle batch headers with no matching lines versus target line_count",
        ),
        make_check(
            "invoices.null_posted_lines",
            source_null_posted,
            target_null_posted,
            "Oracle non-orphan POSTED_YN IS NULL count versus target embedded "
            "posted:null and posted_raw:null count",
        ),
        make_check(
            "invoices.legacy_total_mismatch_lines",
            source_mismatch,
            target_mismatch,
            "Oracle header TOTAL_AMT versus grouped source-line sum; target data_quality/flag count",
        ),
        make_check(
            "invoices.validators_present",
            ["invoices", "invoices_quarantine"],
            validator_actual,
            "target listCollections options.$jsonSchema",
        ),
        make_check(
            "invoices.validator_rejects_bad_document",
            "rejected_code_121",
            probe_actual,
            "target negative insert probe; MongoDB document validation error code 121 and probe absence",
            probe_passed,
        ),
    ]
    passed = all(check["result"] == "pass" for check in checks)
    return {
        "kind": "recon-report",
        "unit": "mongo_invoices",
        "namespace": args.ns,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass",
            "evidence": "fingerprints are replaced by the caller after the second migration run",
        },
        "planted_anomaly_detections": {
            "expected_set": expected_anomaly_set,
            "actual_set": all_anomaly_ids,
            "missing": sorted(set(expected_anomaly_set) - set(all_anomaly_ids)),
            "unexpected": sorted(set(all_anomaly_ids) - set(expected_anomaly_set)),
        },
        "unverified_paths": [
            "Live-Atlas write path and its validator DDL are unverified; only the parent's uncontended Atlas run proves them.",
            "Date NULL and parse-failure branches are unverified because the demo baseline has zero NULL or malformed invoice_dt/due_dt values, so null_invoice_dt, null_due_dt, unparseable_invoice_dt, and unparseable_due_dt never fired.",
            "null_amount, unparseable_amount, and null_invoice_id quarantine branches are unverified because the baseline has zero such rows.",
            "null_required_field quarantine branch is unverified because the baseline has no NULL or unparseable required non-amount line fields.",
            "invalid_encoding quarantine branch is unverified because the baseline has zero non-decodable values.",
            "Malformed GL_ACCT_CSV tolerate-and-attribute path is unverified because baseline CSVs are all well-formed.",
            "Duplicate non-null invoice_no attribution is unverified because the demo baseline has no duplicate INVOICE_NO values.",
        ],
    }, passed


def main() -> int:
    args = parser().parse_args()
    validate_namespace(args.ns)
    batch_no = resolve_batch(args.ns, args.batch_no)
    run_migration(args)
    first_client, first_db = mongo_database(args)
    try:
        first_fingerprint = fingerprint(
            first_db["invoices"], first_db["invoices_quarantine"], args.ns
        )
    finally:
        first_client.close()
    run_migration(args)
    report_data, checks_passed = report(args, batch_no)
    second_client, second_db = mongo_database(args)
    try:
        second_fingerprint = fingerprint(
            second_db["invoices"], second_db["invoices_quarantine"], args.ns
        )
    finally:
        second_client.close()
    report_data["idempotency_rerun"] = {
        "performed": True,
        "result": "pass" if first_fingerprint == second_fingerprint else "fail",
        "evidence": (
            f"fingerprint_a={first_fingerprint}; "
            f"fingerprint_b={second_fingerprint}"
        ),
    }
    if first_fingerprint != second_fingerprint:
        checks_passed = False
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report_data, indent=2) + "\n")
    print(json.dumps(report_data, indent=2))
    print(f"recon report: {output}")
    return 0 if checks_passed else 1


if __name__ == "__main__":
    sys.exit(main())
