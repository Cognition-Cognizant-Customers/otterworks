#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import re
import sys
from collections.abc import Callable, Mapping
from urllib.parse import quote, urlsplit

from pymongo import MongoClient


@dataclass(frozen=True)
class NamespaceCredentials:
    username: str
    password: str
    database_name: str


def resolve_namespace_credentials(
    values: Mapping[str, str],
) -> NamespaceCredentials | None:
    fields = {
        "database_username": values.get("database_username", ""),
        "database_password": values.get("database_password", ""),
        "database_name": values.get("database_name", ""),
    }
    if not any(fields.values()):
        return None
    if not all(fields.values()):
        raise ValueError(
            "namespace Terraform outputs database_username, database_password, "
            "and database_name are required to drop an existing database"
        )
    return NamespaceCredentials(
        username=fields["database_username"],
        password=fields["database_password"],
        database_name=fields["database_name"],
    )


def build_namespace_uri(
    cluster_uri: str,
    username: str,
    password: str,
    database_name: str,
) -> str:
    parsed = urlsplit(cluster_uri)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("MONGODB_ATLAS_URI must contain a cluster host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("MONGODB_ATLAS_URI contains an invalid port") from exc
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    query = f"?{parsed.query}" if parsed.query else ""
    return (
        f"{parsed.scheme}://{quote(username, safe='')}:"
        f"{quote(password, safe='')}@{host}/{query}"
    )


def _read_namespace_credentials(stream) -> NamespaceCredentials | None:
    values = stream.buffer.read().split(b"\0")
    if len(values) != 3:
        raise ValueError("expected three Terraform outputs on stdin")
    return resolve_namespace_credentials(
        {
            "database_username": values[0].decode(),
            "database_password": values[1].decode(),
            "database_name": values[2].decode(),
        }
    )


def drop_namespace_database(
    ns: str,
    cluster_uri: str,
    credentials: NamespaceCredentials | None,
    client_factory: Callable[..., object] = MongoClient,
) -> int:
    expected_database_name = f"ow_tp_{ns.lower()}"
    shared_client = None
    try:
        shared_client = client_factory(cluster_uri, serverSelectionTimeoutMS=10000)
        before = set(shared_client.list_database_names())
        if expected_database_name not in before:
            print(f"MongoDB database {expected_database_name} was already absent")
            after = set(shared_client.list_database_names())
            if expected_database_name in after:
                print(f"negative verification FAILED: {expected_database_name} is still present")
                return 1
            print(f"negative verification: {expected_database_name} is absent")
            return 0
        if credentials is None:
            raise RuntimeError(
                f"Terraform namespace outputs are required because "
                f"{expected_database_name} exists"
            )
        if credentials.database_name != expected_database_name:
            raise RuntimeError(
                f"Terraform database_name does not match NS={ns}"
            )
        namespace_uri = build_namespace_uri(
            cluster_uri,
            credentials.username,
            credentials.password,
            credentials.database_name,
        )
        namespace_client = client_factory(
            namespace_uri,
            serverSelectionTimeoutMS=10000,
        )
        try:
            namespace_client.drop_database(expected_database_name)
        finally:
            namespace_client.close()
        print(f"dropped MongoDB database {expected_database_name}")
        after = set(shared_client.list_database_names())
        if expected_database_name in after:
            print(f"negative verification FAILED: {expected_database_name} is still present")
            return 1
        print(f"negative verification: {expected_database_name} is absent")
        return 0
    finally:
        if shared_client is not None:
            shared_client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", required=True)
    parser.add_argument(
        "--credentials-stdin",
        action="store_true",
        help="read database_username, database_password, and database_name from stdin",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.ns):
        raise SystemExit("NS must contain only letters, digits, and underscores")
    cluster_uri = os.environ.get("MONGODB_ATLAS_URI")
    if not cluster_uri:
        raise SystemExit("MONGODB_ATLAS_URI is required")
    try:
        credentials = (
            _read_namespace_credentials(sys.stdin)
            if args.credentials_stdin
            else resolve_namespace_credentials({})
        )
        return drop_namespace_database(args.ns, cluster_uri, credentials)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit(
            f"MongoDB teardown failed for ow_tp_{args.ns.lower()}: "
            f"{type(exc).__name__}"
        ) from None


if __name__ == "__main__":
    raise SystemExit(main())
