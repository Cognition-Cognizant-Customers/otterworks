"""Shared helpers for the MongoDB platform-showcase tooling.

The Wave 1 units own per-collection migration and reconciliation; this module
only carries what the showcase tooling (validator showcase, aggregation report,
aggregate recon, drift staging) needs in common: the migrated collection
inventory, the namespace field each collection uses, and the recon-failure
webhook transport.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

# Every collection written by the Wave 1 migrations, with the field that carries
# the namespace. `files` tags rows with `tenant`; the rest use `ns`.
MIGRATED_COLLECTIONS: dict[str, str] = {
    "customers": "ns",
    "customers_quarantine": "ns",
    "invoices": "ns",
    "invoices_quarantine": "ns",
    "documents": "ns",
    "document_snapshots": "ns",
    "documents_quarantine": "ns",
    "files": "tenant",
    "files_quarantine": "tenant",
}

WEBHOOK_URL_ENV = "OW_TP_MONGO_RECON_WEBHOOK_URL"
WEBHOOK_SECRET_ENV = "OW_TP_MONGO_RECON_WEBHOOK_SECRET"
WEBHOOK_SECRET_HEADER = "X-Webhook-Secret"


def namespace_filter(collection: str, ns: str) -> dict[str, str]:
    """Return the `{<ns field>: ns}` filter for one migrated collection."""
    try:
        field = MIGRATED_COLLECTIONS[collection]
    except KeyError:
        raise SystemExit(f"{collection} is not a migrated collection") from None
    return {field: ns}


def collection_options(database: Any, name: str) -> dict[str, Any]:
    """Read one collection's options by exact name.

    Filtering by exact name rather than `{"name": {"$in": [...]}}` is required:
    Atlas M0 rejects the `$in` form of a listCollections filter.
    """
    info = next(database.list_collections(filter={"name": name}), None)
    if info is None:
        return {}
    options = info.get("options", {})
    return options if isinstance(options, dict) else {}


def json_schema_validator(database: Any, name: str) -> dict[str, Any] | None:
    """Return the `$jsonSchema` in force for a collection, or None."""
    validator = collection_options(database, name).get("validator", {})
    if isinstance(validator, dict):
        schema = validator.get("$jsonSchema")
        if isinstance(schema, dict):
            return schema
    return None


class WebhookNotConfigured(RuntimeError):
    """The recon-failure webhook URL is absent while a recon failed."""


def post_failure_webhook(payload: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    """POST a recon-failure payload to the Devin automation webhook.

    URL and secret come from the environment only, never from the repository and
    never from a default baked into code. The secret is sent in the automation's
    auth header and is never returned, logged, or echoed back to the caller.
    """
    url = os.environ.get(WEBHOOK_URL_ENV, "").strip()
    if not url:
        raise WebhookNotConfigured(
            f"reconciliation FAILED and {WEBHOOK_URL_ENV} is unset, so the Devin "
            "automation was not notified. Export the webhook URL (and "
            f"{WEBHOOK_SECRET_ENV}) and re-run; the notification is never skipped "
            "silently."
        )
    secret = os.environ.get(WEBHOOK_SECRET_ENV, "").strip()
    if not secret:
        raise WebhookNotConfigured(
            f"reconciliation FAILED and {WEBHOOK_SECRET_ENV} is unset, so the "
            "automation would reject an unauthenticated POST. Export the shared "
            "secret and re-run."
        )
    body = json.dumps(payload, sort_keys=True, default=str).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            WEBHOOK_SECRET_HEADER: secret,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "delivered": 200 <= response.status < 300,
                "status": response.status,
                "response_body": response.read(2048).decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "delivered": False,
            "status": exc.code,
            "response_body": exc.read(2048).decode("utf-8", "replace"),
        }
    except urllib.error.URLError as exc:
        return {"delivered": False, "status": None, "response_body": str(exc.reason)}


def redacted_webhook_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Describe the POST that was (or would be) sent, with the secret redacted."""
    url = os.environ.get(WEBHOOK_URL_ENV, "").strip()
    return {
        "method": "POST",
        "url": url or f"<unset: {WEBHOOK_URL_ENV}>",
        "headers": {
            "Content-Type": "application/json",
            WEBHOOK_SECRET_HEADER: "***REDACTED***",
        },
        "body": payload,
    }
