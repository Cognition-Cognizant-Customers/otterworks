"""Revenue and usage by customer segment, from one MongoDB aggregation pipeline.

In the legacy estate this report needs a four-way join across
`CUSTOMER_MASTER`, `ENTITY_ATTR_VALUE`, `INVOICE_HEADER` and `INVOICE_LINE`,
with the invoice lines living in a table of their own. After the migration the
lines are embedded in their invoice, so the whole report is a single pipeline
over two collections.

Money is Decimal128 end to end: the pipeline sums the migrated `decimal` fields
and this script renders them as exact decimal strings. No float arithmetic
touches a currency value at any point.

The output is deterministic: results are ordered by segment code and the body
carries no wall-clock value, so the same target state always renders byte for
byte the same report, digest included. The generation timestamp is printed on
stdout and recorded in the optional JSON side-output, never in the report.

Usage:
    MONGO_URI=... python3 scripts/tp_mongo/aggregation_report.py --ns demo \
        --out build/tp-mongo/demo.segment-revenue.md
"""

from __future__ import annotations

import argparse
import decimal
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import Decimal128

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mongo_common import (  # noqa: E402
    database_name,
    mongo_client,
    mongo_uri,
    validate_ns,
)
from platform_common import redacted_uri  # noqa: E402

# Legacy SEGMENT_CD is an opaque code in the estate: CUSTOMER_MASTER stores the
# number and no code-description table was ever migrated, so the report reads the
# code out rather than inventing a label for it.
SEGMENT_FIELD = "codes.segment"
CUST_TYPE_FIELD = "codes.cust_type"

ZERO = Decimal128(decimal.Decimal("0"))


def pipeline(ns: str) -> list[dict[str, Any]]:
    """One pipeline: invoices, their embedded lines, joined to their customer.

    `$lookup` resolves the invoice's `cust_id` against the migrated customer
    document, which is the only join left in the report; everything the legacy
    `INVOICE_LINE` table provided is read straight out of the embedded array.
    """
    return [
        {"$match": {"ns": ns}},
        {
            "$lookup": {
                "from": "customers",
                "let": {"customer_id": "$cust_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$_id", "$$customer_id"]}, "ns": ns}},
                    {"$project": {"segment": f"${SEGMENT_FIELD}",
                                  "cust_type": f"${CUST_TYPE_FIELD}"}},
                ],
                "as": "customer",
            }
        },
        {
            "$project": {
                "segment": {"$ifNull": [{"$first": "$customer.segment"}, -1]},
                "cust_type": {"$ifNull": [{"$first": "$customer.cust_type"}, -1]},
                "cust_id": 1,
                "legacy_total_amt": 1,
                "tax_amt": 1,
                "legacy_total_matches_lines": 1,
                "line_count": {"$size": "$lines"},
                "billed_amt": {
                    "$reduce": {
                        "input": "$lines",
                        "initialValue": ZERO,
                        "in": {"$add": ["$$value", {"$ifNull": ["$$this.amount", ZERO]}]},
                    }
                },
                "usage_qty": {
                    "$reduce": {
                        "input": "$lines",
                        "initialValue": ZERO,
                        "in": {"$add": ["$$value", {"$ifNull": ["$$this.qty", ZERO]}]},
                    }
                },
            }
        },
        {
            "$group": {
                "_id": {"segment": "$segment", "cust_type": "$cust_type"},
                "invoices": {"$sum": 1},
                "customers": {"$addToSet": "$cust_id"},
                "legacy_header_revenue": {"$sum": "$legacy_total_amt"},
                "line_revenue": {"$sum": "$billed_amt"},
                "tax": {"$sum": "$tax_amt"},
                "usage_qty": {"$sum": "$usage_qty"},
                "lines": {"$sum": "$line_count"},
                "header_total_mismatches": {
                    "$sum": {"$cond": ["$legacy_total_matches_lines", 0, 1]}
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "segment": "$_id.segment",
                "cust_type": "$_id.cust_type",
                "invoices": 1,
                "customers": {"$size": "$customers"},
                "legacy_header_revenue": 1,
                "line_revenue": 1,
                "tax": 1,
                "usage_qty": 1,
                "lines": 1,
                "header_total_mismatches": 1,
            }
        },
        {"$sort": {"segment": 1, "cust_type": 1}},
    ]


def money(value: Any) -> str:
    """Render a Decimal128 as an exact decimal string, never via float."""
    if value is None:
        return "0.00"
    if isinstance(value, Decimal128):
        return f"{value.to_decimal():.2f}"
    if isinstance(value, decimal.Decimal):
        return f"{value:.2f}"
    raise TypeError(
        f"refusing to render {type(value).__name__} as money: currency values must "
        "stay Decimal128/Decimal so no float rounding can enter the report"
    )


def quantity(value: Any) -> str:
    if isinstance(value, Decimal128):
        return f"{value.to_decimal().normalize():f}"
    if isinstance(value, decimal.Decimal):
        return f"{value.normalize():f}"
    raise TypeError(f"unexpected quantity type {type(value).__name__}")


def total_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def dec(row: dict[str, Any], key: str) -> decimal.Decimal:
        value = row[key]
        return value.to_decimal() if isinstance(value, Decimal128) else decimal.Decimal(0)

    return {
        "segment": "ALL",
        "cust_type": "ALL",
        "customers": sum(int(row["customers"]) for row in rows),
        "invoices": sum(int(row["invoices"]) for row in rows),
        "lines": sum(int(row["lines"]) for row in rows),
        "usage_qty": Decimal128(sum((dec(row, "usage_qty") for row in rows),
                                    decimal.Decimal(0))),
        "legacy_header_revenue": Decimal128(sum((dec(row, "legacy_header_revenue")
                                                 for row in rows), decimal.Decimal(0))),
        "line_revenue": Decimal128(sum((dec(row, "line_revenue") for row in rows),
                                       decimal.Decimal(0))),
        "tax": Decimal128(sum((dec(row, "tax") for row in rows), decimal.Decimal(0))),
        "header_total_mismatches": sum(int(row["header_total_mismatches"])
                                       for row in rows),
    }


def render(ns: str, database: str, rows: list[dict[str, Any]],
           totals: dict[str, Any], pipeline_digest: str) -> str:
    header = (
        "| segment_cd | cust_type_cd | customers | invoices | invoice lines "
        "| usage qty | line revenue | tax | legacy header revenue "
        "| header/line total mismatches |"
    )
    divider = "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [
        f"# OtterWorks revenue and usage by customer segment ({ns})",
        "",
        f"- namespace: `{ns}`",
        f"- database: `{database}`",
        "- source: single MongoDB aggregation pipeline over `invoices` "
        "(embedded lines) joined to `customers`",
        "- legacy equivalent: 4-way join across `CUSTOMER_MASTER`, "
        "`ENTITY_ATTR_VALUE`, `INVOICE_HEADER`, `INVOICE_LINE`",
        "- money: BSON `Decimal128` summed by the server and rendered as exact "
        "decimal strings (no float arithmetic)",
        f"- pipeline digest: `{pipeline_digest}`",
        "",
        header,
        divider,
    ]
    for row in list(rows) + [totals]:
        lines.append(
            f"| {row['segment']} | {row['cust_type']} | {row['customers']} "
            f"| {row['invoices']} | {row['lines']} | {quantity(row['usage_qty'])} "
            f"| {money(row['line_revenue'])} | {money(row['tax'])} "
            f"| {money(row['legacy_header_revenue'])} "
            f"| {row['header_total_mismatches']} |"
        )
    lines += [
        "",
        "`segment_cd`/`cust_type_cd` are the legacy `CUSTOMER_MASTER` codes, "
        "carried across as integers; `-1` means the invoice's `cust_id` does not "
        "resolve to a migrated customer.",
        "`line revenue` is recomputed from the embedded lines; `legacy header "
        "revenue` is the legacy `INVOICE_HEADER.TOTAL_AMT` as it stood in Oracle. "
        "`header/line total mismatches` counts invoices where the two disagree - a "
        "legacy inconsistency the migration records rather than papers over.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument(
        "--out",
        default="build/tp-mongo/segment-revenue.md",
        help="write the rendered report here (generated, never committed)",
    )
    parser.add_argument("--json-out", help="also write the report rows as JSON")
    args = parser.parse_args()
    ns = validate_ns(args.ns)

    client = mongo_client()
    try:
        database = client[database_name(ns)]
        stages = pipeline(ns)
        rows = list(database["invoices"].aggregate(stages))
    finally:
        client.close()

    if not rows:
        raise SystemExit(
            f"no invoices found for ns={ns} in {database_name(ns)}: run the "
            f"migrations first (make tp-mongo-invoices NS={ns})"
        )

    pipeline_digest = hashlib.md5(
        json.dumps(stages, sort_keys=True, default=str).encode()
    ).hexdigest()
    totals = total_row(rows)
    body = render(ns, database_name(ns), rows, totals, pipeline_digest)
    report_digest = hashlib.md5(body.encode()).hexdigest()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"{body}\n<!-- report_digest: {report_digest} -->\n", encoding="utf-8")

    print(body, end="")
    print(f"\nreport_digest={report_digest}  generated_at="
          f"{datetime.now(timezone.utc).isoformat()}  uri={redacted_uri(mongo_uri())}  wrote {out}")

    if args.json_out:
        json_out = Path(args.json_out)
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps(
                {
                    "namespace": ns,
                    "database": database_name(ns),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "pipeline_digest": pipeline_digest,
                    "report_digest": report_digest,
                    "rows": [
                        {
                            "segment": row["segment"],
                            "cust_type": row["cust_type"],
                            "customers": int(row["customers"]),
                            "invoices": int(row["invoices"]),
                            "lines": int(row["lines"]),
                            "usage_qty": quantity(row["usage_qty"]),
                            "legacy_header_revenue": money(row["legacy_header_revenue"]),
                            "line_revenue": money(row["line_revenue"]),
                            "tax": money(row["tax"]),
                            "header_total_mismatches": int(row["header_total_mismatches"]),
                        }
                        for row in list(rows) + [totals]
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
