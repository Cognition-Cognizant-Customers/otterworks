#!/usr/bin/env python3
"""Atlas Search index definitions for the Cron Box search collections.

The definitions themselves live as code under
infrastructure/atlas/cronbox/search-indexes/ and replace the legacy
MeiliSearch settings patch. This module loads them, describes the intended
operations for a child-safe dry run, applies them idempotently (parent only),
and reads the deployed definitions back through the PyMongo data plane so recon
never trusts local state.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS_DIR = REPO_ROOT / "infrastructure" / "atlas" / "cronbox" / "search-indexes"
URI_ENV = "MONGODB_ATLAS_URI"

# MeiliSearch attribute role -> Atlas Search index types that satisfy it.
# "searchable" needs an analyzed string; MeiliSearch filter/sort equality is
# exact, so those roles need a token (string), date, or number mapping.
SEARCHABLE_TYPES = {"string", "autocomplete"}
EXACT_TYPES = {"token", "date", "number", "boolean", "objectId"}

ROLES: dict[str, dict[str, tuple[str, ...]]] = {
    "documents": {
        "searchable": ("title", "content", "tags"),
        "filterable": ("type", "owner_id", "tags", "created_at", "updated_at"),
        "sortable": ("updated_at", "created_at"),
    },
    "files": {
        "searchable": ("name", "tags", "mime_type"),
        "filterable": (
            "type",
            "owner_id",
            "mime_type",
            "folder_id",
            "tags",
            "created_at",
            "updated_at",
        ),
        "sortable": ("updated_at", "created_at", "size"),
    },
}


def load_definitions(directory: Path = DEFINITIONS_DIR) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def field_types(definition: dict[str, Any], field: str) -> set[str]:
    """The set of Atlas Search index types mapped for one field."""
    mappings = definition.get("definition", definition).get("mappings", {})
    mapped = mappings.get("fields", {}).get(field)
    if mapped is None:
        return set()
    if isinstance(mapped, dict):
        mapped = [mapped]
    return {entry.get("type", "") for entry in mapped}


def role_violations(definition: dict[str, Any]) -> list[str]:
    """Report every MeiliSearch attribute role the definition fails to preserve."""
    collection = definition.get("collectionName", "")
    roles = ROLES.get(collection)
    if roles is None:
        return [
            f"{collection}: no expected field roles are declared for this collection"
        ]
    problems = []
    for field in roles["searchable"]:
        if not field_types(definition, field) & SEARCHABLE_TYPES:
            problems.append(
                f"{collection}.{field}: searchable role needs an analyzed string type"
            )
    for role in ("filterable", "sortable"):
        for field in roles[role]:
            if not field_types(definition, field) & EXACT_TYPES:
                problems.append(
                    f"{collection}.{field}: {role} role needs an exact "
                    f"({'/'.join(sorted(EXACT_TYPES))}) type"
                )
    if definition.get("name") != "default":
        problems.append(f"{collection}: index name must be 'default'")
    if not definition.get("database", "").startswith("ow_tp"):
        problems.append(f"{collection}: database must be ow_tp-prefixed")
    return problems


def intended_operations(definitions: Sequence[dict[str, Any]]) -> Iterator[str]:
    for definition in definitions:
        yield (
            f"ensure search index {definition['name']} on "
            f"{definition['database']}.{definition['collectionName']} "
            f"({len(definition['definition']['mappings']['fields'])} mapped fields)"
        )


def _client(uri: str | None = None) -> Any:
    from pymongo import MongoClient

    uri = uri or os.environ.get(URI_ENV)
    if not uri:
        raise SystemExit(f"{URI_ENV} is required for Atlas index management")
    return MongoClient(uri, serverSelectionTimeoutMS=20_000)


def _read_back_collection(
    client: Any, database: str, collection: str
) -> list[dict[str, Any]]:
    """Read Atlas Search definitions and build status from the PyMongo data plane."""
    result = []
    for item in client[database][collection].list_search_indexes():
        definition = item.get("latestDefinition") or item.get("definition") or {}
        result.append(
            {
                "database": database,
                "collectionName": collection,
                "name": item.get("name"),
                "indexID": item.get("indexID"),
                "status": item.get("status"),
                "queryable": item.get("queryable"),
                "definition": definition,
            }
        )
    return result


def read_back(
    database: str, collection: str, uri: str | None = None
) -> list[dict[str, Any]]:
    """Read deployed search index definitions through PyMongo."""
    client = _client(uri)
    try:
        return _read_back_collection(client, database, collection)
    finally:
        client.close()


def apply_definitions(definitions: Sequence[dict[str, Any]]) -> list[str]:
    """Create or update each index in place; never deletes an index (parent only)."""
    from pymongo.operations import SearchIndexModel

    client = _client()
    outcomes = []
    try:
        for definition in definitions:
            collection = client[definition["database"]][definition["collectionName"]]
            existing = {
                item.get("name"): item for item in collection.list_search_indexes()
            }
            name = definition["name"]
            if name not in existing:
                collection.create_search_index(
                    SearchIndexModel(
                        definition=definition["definition"],
                        name=name,
                        type=definition.get("type", "search"),
                    )
                )
                action = "created"
            else:
                collection.update_search_index(name, definition["definition"])
                action = "updated"
            outcomes.append(
                f"{action} {name} on "
                f"{definition['database']}.{definition['collectionName']}"
            )

        for definition in definitions:
            deployed = _read_back_collection(
                client, definition["database"], definition["collectionName"]
            )
            current = next(
                (item for item in deployed if item["name"] == definition["name"]),
                None,
            )
            if current is None:
                raise RuntimeError(
                    f"search index {definition['name']} on "
                    f"{definition['database']}.{definition['collectionName']} "
                    "was not returned by list_search_indexes"
                )
            problems = role_violations(current)
            if problems:
                raise RuntimeError(
                    f"role verification failed for {definition['name']}: "
                    + "; ".join(problems)
                )
            status = current.get("status") or "unknown"
            queryable = current.get("queryable")
            outcomes.append(
                f"verified {definition['name']} on "
                f"{definition['database']}.{definition['collectionName']} "
                f"preserves every MeiliSearch attribute role "
                f"(status={status}, queryable={queryable})"
            )
    finally:
        client.close()
    return outcomes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the definitions offline and print the intended operations",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="parent-owned idempotent index create/update",
    )
    mode.add_argument(
        "--read-back",
        action="store_true",
        help="print the deployed definitions and build status from PyMongo",
    )
    args = parser.parse_args(argv)

    definitions = load_definitions()
    if not definitions:
        raise SystemExit(f"no index definitions found under {DEFINITIONS_DIR}")

    problems = [
        problem for definition in definitions for problem in role_violations(definition)
    ]
    if problems:
        for problem in problems:
            print(f"role violation: {problem}")
        return 1

    if args.apply:
        for outcome in apply_definitions(definitions):
            print(outcome)
        return 0

    if args.read_back:
        deployed = {
            f"{definition['database']}.{definition['collectionName']}": read_back(
                definition["database"], definition["collectionName"]
            )
            for definition in definitions
        }
        print(json.dumps(deployed, indent=2, sort_keys=True, default=str))
        return 0

    for operation in intended_operations(definitions):
        print(operation)
    print(f"{len(definitions)} definition(s) preserve every MeiliSearch attribute role")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
