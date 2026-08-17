#!/usr/bin/env python3
"""Atlas Search index definitions for the Cron Box search collections.

The definitions themselves live as code under
infrastructure/atlas/cronbox/search-indexes/ and replace the legacy
MeiliSearch settings patch. This module loads them, describes the intended
operations for a child-safe dry run, applies them idempotently (parent only),
and reads the deployed definitions back from the Atlas Admin API so recon never
trusts local state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS_DIR = REPO_ROOT / "infrastructure" / "atlas" / "cronbox" / "search-indexes"
API_BASE = "https://cloud.mongodb.com/api/atlas/v2"
API_VERSION = "application/vnd.atlas.2024-05-30+json"
PROJECT_ENV = "MONGODB_ATLAS_PROJECT_ID"
PUBLIC_KEY_ENV = "MONGODB_ATLAS_PUBLIC_KEY"
PRIVATE_KEY_ENV = "MONGODB_ATLAS_PRIVATE_KEY"
CLUSTER_ENV = "MONGODB_ATLAS_CLUSTER_NAME"

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


def _auth() -> Any:
    from requests.auth import HTTPDigestAuth

    missing = [
        name
        for name in (PUBLIC_KEY_ENV, PRIVATE_KEY_ENV, PROJECT_ENV)
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(
            f"missing required environment variable(s): {', '.join(missing)}"
        )
    return HTTPDigestAuth(os.environ[PUBLIC_KEY_ENV], os.environ[PRIVATE_KEY_ENV])


def _project() -> str:
    project = os.environ[PROJECT_ENV]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
        raise SystemExit(f"{PROJECT_ENV} must contain only letters, digits, '_' or '-'")
    return project


def _headers() -> dict[str, str]:
    return {"Accept": API_VERSION, "Content-Type": "application/json"}


def cluster_name(auth: Any = None) -> str:
    """The configured cluster, or the project's single cluster when unambiguous."""
    import requests

    configured = os.environ.get(CLUSTER_ENV)
    if configured:
        return configured
    response = requests.get(
        f"{API_BASE}/groups/{_project()}/clusters",
        auth=auth or _auth(),
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    names = [item["name"] for item in response.json().get("results", [])]
    if len(names) != 1:
        raise SystemExit(
            f"set {CLUSTER_ENV}: the project has {len(names)} clusters, so the target is ambiguous"
        )
    return names[0]


def read_back(
    database: str, collection: str, auth: Any = None, cluster: str | None = None
) -> list[dict[str, Any]]:
    """Read deployed search index definitions from the Atlas Admin API."""
    import requests

    auth = auth or _auth()
    cluster = cluster or cluster_name(auth)
    url = (
        f"{API_BASE}/groups/{_project()}/clusters/{urllib.parse.quote(cluster, safe='')}"
        f"/search/indexes/{urllib.parse.quote(database, safe='')}"
        f"/{urllib.parse.quote(collection, safe='')}"
    )
    response = requests.get(url, auth=auth, headers=_headers(), timeout=30)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, list) else body.get("results", [])


def apply_definitions(definitions: Sequence[dict[str, Any]]) -> list[str]:
    """Create or update each index in place; never deletes an index (parent only)."""
    import requests

    auth = _auth()
    cluster = cluster_name(auth)
    project = _project()
    base = f"{API_BASE}/groups/{project}/clusters/{urllib.parse.quote(cluster, safe='')}/search/indexes"
    outcomes = []
    for definition in definitions:
        existing = {
            item.get("name"): item
            for item in read_back(
                definition["database"],
                definition["collectionName"],
                auth=auth,
                cluster=cluster,
            )
        }
        current = existing.get(definition["name"])
        if current is None:
            response = requests.post(
                base, auth=auth, headers=_headers(), json=definition, timeout=60
            )
            action = "created"
        else:
            response = requests.patch(
                f"{base}/{urllib.parse.quote(str(current['indexID']), safe='')}",
                auth=auth,
                headers=_headers(),
                json={
                    "database": definition["database"],
                    "collectionName": definition["collectionName"],
                    "name": definition["name"],
                    "type": definition.get("type", "search"),
                    "definition": definition["definition"],
                },
                timeout=60,
            )
            action = "updated"
        response.raise_for_status()
        outcomes.append(
            f"{action} {definition['name']} on "
            f"{definition['database']}.{definition['collectionName']}"
        )
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
        help="print the deployed definitions as reported by the Atlas Admin API",
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
