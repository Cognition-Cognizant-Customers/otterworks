import importlib.util
from pathlib import Path

import pytest

APP_MODULE = importlib.util.spec_from_file_location(
    "legacy_billing_app", Path(__file__).parents[1] / "app" / "app.py"
)
assert APP_MODULE is not None
assert APP_MODULE.loader is not None
legacy_billing_app = importlib.util.module_from_spec(APP_MODULE)
APP_MODULE.loader.exec_module(legacy_billing_app)
app = legacy_billing_app.app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(legacy_billing_app, "select", lambda sql, params=(): [])
    return app.test_client()


def test_health_does_not_require_database(client):
    response = client.get("/health")

    assert response.status_code < 500
