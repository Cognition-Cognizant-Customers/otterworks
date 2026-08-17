"""Demonstrate the `$jsonSchema` validators in force on the migrated collections.

For every migrated collection this prints the validator MongoDB is enforcing and
then tries to write documents that violate it, showing the server's own refusal.
Nothing here pre-checks a document in Python: each probe is handed to MongoDB and
the printed verdict is whatever the server answered. A probe that the server
*accepts* is a failure of the showcase (the validator is not doing its job), and
only in that case is the accepted document deleted again.

Usage:
    MONGO_URI=... python3 scripts/tp_mongo/validator_showcase.py --ns demo
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from bson.json_util import CANONICAL_JSON_OPTIONS
from bson.json_util import dumps as bson_dumps
from pymongo.errors import OperationFailure, WriteError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mongo_common import (  # noqa: E402
    database_name,
    mongo_client,
    mongo_uri,
    validate_ns,
)
from platform_common import (  # noqa: E402
    MIGRATED_COLLECTIONS,
    json_schema_validator,
    namespace_filter,
    redacted_uri,
)

PROBE_ID_PREFIX = "ow_tp_validator_showcase"

# One typed field per collection whose BSON type the validator constrains, with a
# value of the wrong type. These are the legacy shapes the migration refuses to
# carry over: a dirty date string, float money, Y/N in place of a boolean.
WRONG_TYPE_PROBES: dict[str, tuple[str, Any, str]] = {
    "customers": ("signup_date", "31-FEB-24", "legacy DD-MON-YY text where a BSON date is required"),
    "customers_quarantine": ("schema_version", "1", "text where an int is required"),
    "invoices": ("total_amt", 1234.56, "IEEE-754 double money where Decimal128 is required"),
    "invoices_quarantine": ("amount", 1234.56, "double money where Decimal128 is required"),
    "documents": ("is_deleted", "N", "legacy Y/N text where a bool is required"),
    "document_snapshots": ("created_at", "2026-08-01", "text where a BSON date is required"),
    "documents_quarantine": ("reason", 7, "int where the quarantine reason must be text"),
    "files": ("size_bytes", "12345", "text where a numeric byte count is required"),
    "files_quarantine": ("reason", 7, "int where the quarantine reason must be text"),
}


def summarize_validator(schema: dict[str, Any]) -> str:
    properties = schema.get("properties", {})
    typed = []
    for name, spec in properties.items():
        if isinstance(spec, dict) and "bsonType" in spec:
            typed.append(f"{name}:{spec['bsonType']}")
    lines = [
        f"    required ({len(schema.get('required', []))}): "
        f"{', '.join(schema.get('required', []))}",
        f"    additionalProperties: {schema.get('additionalProperties')}",
        f"    typed properties ({len(typed)}): {', '.join(typed[:12])}"
        + (" ..." if len(typed) > 12 else ""),
    ]
    return "\n".join(lines)


def attempt(collection: Any, document: dict[str, Any]) -> dict[str, Any]:
    """Insert a document and report what the SERVER did with it."""
    try:
        collection.insert_one(document)
    except (WriteError, OperationFailure) as exc:
        details = getattr(exc, "details", None) or {}
        err_info = details.get("errInfo", {})
        return {
            "rejected_by_server": True,
            "code": getattr(exc, "code", None),
            "code_name": details.get("codeName"),
            "server_message": str(exc).split("full error")[0].strip(),
            "failing_document_id": document["_id"],
            "server_err_info": err_info,
        }
    # Accepted: the validator did not hold. Remove the row we should not have
    # been able to write, and report the showcase failure.
    collection.delete_one({"_id": document["_id"]})
    return {
        "rejected_by_server": False,
        "code": None,
        "code_name": None,
        "server_message": "ACCEPTED - the server did not enforce the validator",
        "failing_document_id": document["_id"],
        "server_err_info": {},
    }


def minimal_probe(collection: str, ns: str, probe_id: str) -> dict[str, Any]:
    document: dict[str, Any] = {"_id": probe_id}
    document.update(namespace_filter(collection, ns))
    return document


def mutate(document: dict[str, Any], field: str, value: Any) -> dict[str, Any]:
    probe = copy.deepcopy(document)
    probe[field] = value
    return probe


def sample_document(database: Any, collection: str, ns: str) -> dict[str, Any] | None:
    return database[collection].find_one(namespace_filter(collection, ns))


def print_error(label: str, outcome: dict[str, Any]) -> None:
    verdict = ("REJECTED by MongoDB" if outcome["rejected_by_server"]
               else "ACCEPTED (showcase FAILED)")
    code = outcome["code"]
    suffix = f" [code {code} {outcome['code_name'] or ''}]" if code else ""
    print(f"    {label}: {verdict}{suffix}")
    print(f"      server said: {outcome['server_message'][:400]}")
    failing = outcome["server_err_info"].get("details", {}).get("schemaRulesNotSatisfied")
    if failing:
        rendered = bson_dumps(failing, json_options=CANONICAL_JSON_OPTIONS)
        print(f"      schema rules not satisfied: {rendered[:600]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument(
        "--json-out",
        help="also write the machine-readable showcase result to this path",
    )
    args = parser.parse_args()
    ns = validate_ns(args.ns)

    client = mongo_client()
    failures: list[str] = []
    results: list[dict[str, Any]] = []
    try:
        database = client[database_name(ns)]
        print(f"MongoDB server-side validators in force  uri={redacted_uri(mongo_uri())} "
              f"db={database_name(ns)} ns={ns}")
        for collection in MIGRATED_COLLECTIONS:
            print(f"\n== {collection}")
            schema = json_schema_validator(database, collection)
            if schema is None:
                print("    NO $jsonSchema VALIDATOR IN FORCE")
                failures.append(f"{collection}: no validator")
                results.append({"collection": collection, "validator_present": False,
                                "probes": []})
                continue
            print(summarize_validator(schema))
            probes: list[dict[str, Any]] = []

            outcome = attempt(
                database[collection],
                minimal_probe(collection, ns, f"{PROBE_ID_PREFIX}_missing_required"),
            )
            print_error("missing required fields", outcome)
            probes.append({"probe": "missing_required_fields", **outcome})

            sample = sample_document(database, collection, ns)
            if sample is None:
                print("    (collection is empty for this namespace: "
                      "type and unmodeled-field probes need a real document)")
                if schema.get("additionalProperties") is not False:
                    print("    field absent from the schema: NOT ASSERTED - this "
                          "validator does not set additionalProperties:false, so "
                          "unmodeled fields are permitted by design")
                    probes.append({"probe": "unmodeled_field", "attempted": False,
                                   "rejected_by_server": None,
                                   "reason": "validator permits additional properties"})
            else:
                field, bad_value, why = WRONG_TYPE_PROBES[collection]
                probe = mutate(sample, "_id", f"{PROBE_ID_PREFIX}_wrong_type")
                probe = mutate(probe, field, bad_value)
                outcome = attempt(database[collection], probe)
                print_error(f"wrong BSON type on {field} ({why})", outcome)
                probes.append({"probe": f"wrong_bson_type:{field}", **outcome})

                if schema.get("additionalProperties") is False:
                    probe = mutate(sample, "_id", f"{PROBE_ID_PREFIX}_unmodeled_field")
                    probe = mutate(probe, "legacy_udf_dump", "carried over by accident")
                    outcome = attempt(database[collection], probe)
                    print_error("field absent from the schema", outcome)
                    probes.append({"probe": "unmodeled_field", **outcome})
                else:
                    # This validator deliberately leaves the document open (the files
                    # unit carries attributed DynamoDB extras), so an unmodeled field
                    # is not a violation here and is not asserted as one.
                    print("    field absent from the schema: NOT ASSERTED - this "
                          "validator does not set additionalProperties:false, so "
                          "unmodeled fields are permitted by design")
                    probes.append({"probe": "unmodeled_field", "attempted": False,
                                   "rejected_by_server": None,
                                   "reason": "validator permits additional properties"})

            for entry in probes:
                if entry["rejected_by_server"] is False:
                    failures.append(f"{collection}: {entry['probe']} was accepted")
            results.append({"collection": collection, "validator_present": True,
                            "probes": probes})
    finally:
        client.close()

    print("\n" + "=" * 72)
    if failures:
        print(f"validator showcase FAILED ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
    else:
        print(f"validator showcase PASSED: {len(MIGRATED_COLLECTIONS)} collections, "
              "every non-conforming write refused by the server")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "namespace": ns,
                    "database": database_name(ns),
                    "collections": [
                        {
                            "collection": entry["collection"],
                            "validator_present": entry["validator_present"],
                            "probes": [
                                {
                                    "probe": probe["probe"],
                                    "rejected_by_server": probe["rejected_by_server"],
                                    "code": probe.get("code"),
                                    "code_name": probe.get("code_name"),
                                }
                                for probe in entry["probes"]
                            ],
                        }
                        for entry in results
                    ],
                    "failures": failures,
                },
                handle,
                indent=2,
                sort_keys=True,
            )
        print(f"wrote {args.json_out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
