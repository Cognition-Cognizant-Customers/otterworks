from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tp_atlas_teardown import (  # noqa: E402
    NamespaceCredentials,
    _read_namespace_credentials,
    build_namespace_uri,
    drop_namespace_database,
    resolve_namespace_credentials,
)


class FakeClient:
    def __init__(self, databases: set[str], uri: str) -> None:
        self.databases = databases
        self.uri = uri
        self.dropped: list[str] = []

    def list_database_names(self) -> list[str]:
        return sorted(self.databases)

    def drop_database(self, name: str) -> None:
        self.databases.discard(name)
        self.dropped.append(name)

    def close(self) -> None:
        pass


def test_resolve_namespace_credentials_requires_all_outputs() -> None:
    assert resolve_namespace_credentials({}) is None
    assert resolve_namespace_credentials(
        {
            "database_username": "ow-tp-drift01",
            "database_password": "fake-password",
            "database_name": "ow_tp_drift01",
        }
    ) == NamespaceCredentials(
        "ow-tp-drift01",
        "fake-password",
        "ow_tp_drift01",
    )
    try:
        resolve_namespace_credentials(
            {
                "database_username": "ow-tp-drift01",
                "database_password": "",
                "database_name": "ow_tp_drift01",
            }
        )
    except ValueError as exc:
        assert "database_password" in str(exc)
    else:
        raise AssertionError("incomplete Terraform outputs must fail")


def test_build_namespace_uri_uses_cluster_host_and_quotes_credentials() -> None:
    uri = build_namespace_uri(
        "mongodb+srv://shared:ignored@cluster.example/ow_tp_shared?retryWrites=true",
        "ow-tp-drift01",
        "fake/p@ss",
        "ow_tp_drift01",
    )
    assert uri == (
        "mongodb+srv://ow-tp-drift01:fake%2Fp%40ss@cluster.example/"
        "?retryWrites=true"
    )


def test_read_namespace_credentials_from_stdin() -> None:
    stream = io.TextIOWrapper(
        io.BytesIO(b"ow-tp-drift01\0fake-password\0ow_tp_drift01"),
    )
    assert _read_namespace_credentials(stream) == NamespaceCredentials(
        "ow-tp-drift01",
        "fake-password",
        "ow_tp_drift01",
    )


def test_drop_namespace_database_uses_namespace_client() -> None:
    clients: list[FakeClient] = []
    databases = {"ow_tp_drift01"}

    def factory(uri: str, **_: object) -> FakeClient:
        client = FakeClient(databases, uri)
        clients.append(client)
        return client

    result = drop_namespace_database(
        "drift01",
        "mongodb+srv://shared:ignored@cluster.example/ow_tp_shared",
        NamespaceCredentials("ow-tp-drift01", "fake-password", "ow_tp_drift01"),
        factory,
    )

    assert result == 0
    assert len(clients) == 2
    assert clients[0].uri.startswith("mongodb+srv://shared:ignored@")
    assert clients[1].uri.startswith("mongodb+srv://ow-tp-drift01:")
    assert clients[1].dropped == ["ow_tp_drift01"]


def test_already_absent_database_does_not_require_namespace_outputs() -> None:
    clients: list[FakeClient] = []
    databases: set[str] = set()

    def factory(uri: str, **_: object) -> FakeClient:
        client = FakeClient(databases, uri)
        clients.append(client)
        return client

    result = drop_namespace_database(
        "drift01",
        "mongodb+srv://shared:ignored@cluster.example/ow_tp_shared",
        None,
        factory,
    )

    assert result == 0
    assert len(clients) == 1


def test_present_database_without_namespace_outputs_fails_loudly() -> None:
    databases = {"ow_tp_drift01"}

    def factory(uri: str, **_: object) -> FakeClient:
        return FakeClient(databases, uri)

    try:
        drop_namespace_database(
            "drift01",
            "mongodb+srv://shared:ignored@cluster.example/ow_tp_shared",
            None,
            factory,
        )
    except RuntimeError as exc:
        assert "Terraform namespace outputs" in str(exc)
    else:
        raise AssertionError("existing database without credentials must fail")


def test_main_reports_truncated_credentials_without_traceback(monkeypatch) -> None:
    monkeypatch.setenv(
        "MONGODB_ATLAS_URI",
        "mongodb+srv://shared:ignored@cluster.example/ow_tp_shared",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["tp_atlas_teardown.py", "--ns", "drift01", "--credentials-stdin"],
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(io.BytesIO(b"ow-tp-drift01\0fake-password")),
    )

    from tp_atlas_teardown import main

    with pytest.raises(SystemExit, match="expected three Terraform outputs"):
        main()


def test_main_reports_invalid_credential_encoding_without_traceback(monkeypatch) -> None:
    monkeypatch.setenv(
        "MONGODB_ATLAS_URI",
        "mongodb+srv://shared:ignored@cluster.example/ow_tp_shared",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["tp_atlas_teardown.py", "--ns", "drift01", "--credentials-stdin"],
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(io.BytesIO(b"\xff\0fake-password\0ow_tp_drift01")),
    )

    from tp_atlas_teardown import main

    with pytest.raises(SystemExit, match="utf-8"):
        main()
