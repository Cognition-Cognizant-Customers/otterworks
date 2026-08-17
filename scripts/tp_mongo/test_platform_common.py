from platform_common import redacted_uri, redacted_uri_for_report


def test_redacted_uri_with_srv_credentials() -> None:
    uri = "mongodb+srv://alice:pa%40ss%2Fword@cluster.example/ow_tp_demo?retryWrites=true"
    assert (
        redacted_uri(uri)
        == "mongodb+srv://alice:<redacted>@cluster.example/ow_tp_demo?retryWrites=true"
    )


def test_redacted_uri_with_plain_credentials() -> None:
    uri = "mongodb://alice:sup3rs3cret@localhost:27017/ow_tp_demo"
    assert redacted_uri(uri) == "mongodb://alice:<redacted>@localhost:27017/ow_tp_demo"
    assert redacted_uri_for_report(uri) == "mongodb://localhost:27017/ow_tp_demo"


def test_redacted_uri_without_credentials() -> None:
    uri = "mongodb://localhost:27017/ow_tp_demo"
    assert redacted_uri(uri) == uri


def test_redacted_uri_hides_url_escaped_password() -> None:
    uri = "mongodb://alice:p%40ss%2Fword@localhost:27017/ow_tp_demo"
    rendered = redacted_uri(uri)
    assert rendered == "mongodb://alice:<redacted>@localhost:27017/ow_tp_demo"
    assert "%40" not in rendered
    assert "%2F" not in rendered


def test_redacted_uri_fails_closed_for_unescaped_password_delimiters() -> None:
    cases = {
        "pa/ss": "mongodb://alice:pa/ss@localhost:27017/ow_tp_redact",
        "pa?ss": "mongodb://alice:pa?ss@localhost:27017/ow_tp_redact",
        "pa#ss": "mongodb://alice:pa#ss@localhost:27017/ow_tp_redact",
        "1234/5": "mongodb://alice:1234/5@localhost:27017/ow_tp_redact",
    }
    for secret, uri in cases.items():
        for rendered in (redacted_uri(uri), redacted_uri_for_report(uri)):
            assert rendered == "mongodb://<unparseable-uri-withheld>"
            assert secret not in rendered
            assert "localhost:27017" not in rendered


def test_redacted_uri_with_later_at_is_fully_withheld() -> None:
    uri = "mongodb://alice:pa@ss/word@host:27017/db"
    expected = "mongodb://<unparseable-uri-withheld>"
    for rendered in (redacted_uri(uri), redacted_uri_for_report(uri)):
        assert rendered == expected
        assert "ss/word" not in rendered
        assert "ss" not in rendered


def test_redacted_uri_for_report_removes_userinfo() -> None:
    uri = "mongodb+srv://alice:pa%40ss%2Fword@cluster.example/ow_tp_redact"
    rendered = redacted_uri_for_report(uri)
    assert rendered == "mongodb+srv://cluster.example/ow_tp_redact"
    assert "alice" not in rendered
    assert "pa%40ss%2Fword" not in rendered


def test_credential_free_at_in_query_is_withheld() -> None:
    uri = "mongodb://localhost:27017/db?appName=a@b"
    expected = "mongodb://<unparseable-uri-withheld>"
    assert redacted_uri(uri) == expected
    assert redacted_uri_for_report(uri) == expected


def test_schemeless_credentials_are_withheld() -> None:
    uri = "alice:s3cret@cluster.example/db"
    expected = "<unparseable-uri-withheld>"
    for rendered in (redacted_uri(uri), redacted_uri_for_report(uri)):
        assert rendered == expected
        assert "s3cret" not in rendered
        assert "cluster.example" not in rendered


def test_schemeless_credential_free_uri_is_unchanged() -> None:
    uri = "localhost:27017"
    assert redacted_uri(uri) == uri
    assert redacted_uri_for_report(uri) == uri
