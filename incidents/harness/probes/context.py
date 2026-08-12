"""Shared probe context: target, seeded identities, and HTTP helpers.

Every probe drives the *running* application through the API gateway, using
real accounts registered at run time, namespaced by ``run_id`` so concurrent
runs (CI, several sessions, several tenants) never collide.
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx


def run_password() -> str:
    """A single-use password for an account the run registers.

    The accounts are real and outlive the run, so each one gets its own value
    that exists only in the running process.
    """
    return f"Incident-{secrets.token_urlsafe(24)}-1!"


@dataclass
class Identity:
    email: str
    password: str
    user_id: str = ""
    access_token: str = ""

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}


class SetupError(RuntimeError):
    """Raised when the run cannot establish the identities it needs."""


CHAOS_SCENARIOS = {
    "search-service:suggest_500": ("search-service", "suggest_500"),
    "file-service:upload_s3_error": ("file-service", "upload_s3_error"),
    "document-service:slow_queries": ("document-service", "slow_queries"),
    "notification-service:consumer_strict_schema": (
        "notification-service",
        "consumer_strict_schema",
    ),
}


@dataclass
class IncidentContext:
    base_url: str
    client: httpx.Client
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    #: The reporter drives every symptom endpoint.
    reporter: Identity = field(init=False)
    #: The recipient exists so the notification scenario has a real second
    #: user whose inbox the shared-file event must land in.
    recipient: Identity = field(init=False)

    def __post_init__(self) -> None:
        self.reporter = Identity(
            email=f"incident-reporter-{self.run_id}@example.test", password=run_password()
        )
        self.recipient = Identity(
            email=f"incident-recipient-{self.run_id}@example.test", password=run_password()
        )

    # ── lifecycle ────────────────────────────────────────────────────────────

    def wait_for_target(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if self.client.get("/health").status_code < 500:
                    return
            except httpx.HTTPError as exc:
                last = exc
            time.sleep(1.0)
        raise SetupError(f"target {self.base_url} did not become reachable: {last}")

    def seed_identities(self, timeout: float = 120.0) -> None:
        """Register the run's accounts, waiting for the auth backend to come up.

        The gateway's ``/health`` is a static handler, so it answers well
        before auth-service can serve a registration; registration is the real
        readiness check, so it is the one retried.
        """
        deadline = time.monotonic() + timeout
        for identity in (self.reporter, self.recipient):
            while True:
                try:
                    self._register(identity)
                    break
                except SetupError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(2.0)

    @property
    def identities_ready(self) -> bool:
        return all(
            identity.access_token and identity.user_id
            for identity in (self.reporter, self.recipient)
        )

    def _register(self, identity: Identity) -> None:
        try:
            response = self.client.post(
                "/api/v1/auth/register",
                json={
                    "email": identity.email,
                    "password": identity.password,
                    "displayName": f"Incident {identity.email.split('@')[0]}",
                },
            )
        except httpx.HTTPError as exc:
            raise SetupError(f"could not reach registration for {identity.email}: {exc}") from exc
        if response.status_code not in (200, 201):
            raise SetupError(
                f"could not register {identity.email}: "
                f"{response.status_code} {response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise SetupError(f"registration for {identity.email} returned non-JSON") from exc
        if not isinstance(body, dict):
            raise SetupError(f"registration for {identity.email} returned {type(body).__name__}")
        identity.access_token = body.get("accessToken", "")
        user = body.get("user")
        identity.user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        if not identity.access_token or not identity.user_id:
            raise SetupError(f"registration for {identity.email} returned no usable identity")

    # ── chaos controls (injection only — probes never read the flag) ─────────

    def _chaos_headers(self) -> dict[str, str]:
        headers = dict(self.reporter.headers)
        chaos_secret = os.getenv("CHAOS_SECRET", "")
        if chaos_secret:
            headers["X-Chaos-Secret"] = chaos_secret
        return headers

    def inject(self, scenario_id: str) -> httpx.Response:
        service, scenario = CHAOS_SCENARIOS[scenario_id]
        return self.client.post(
            "/api/v1/admin/chaos",
            headers=self._chaos_headers(),
            json={"service": service, "scenario": scenario},
        )

    def reset(self) -> httpx.Response:
        return self.client.delete("/api/v1/admin/chaos", headers=self._chaos_headers())

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        identity: Identity | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
    ) -> httpx.Response:
        merged = dict(identity.headers) if identity else {}
        merged.update(headers or {})
        return self.client.request(
            method, path, headers=merged, params=params, json=json, files=files
        )

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def upload_file(self, identity: Identity, name: str, content: bytes) -> httpx.Response:
        return self.request(
            "POST",
            "/api/v1/files/upload",
            identity=identity,
            files={"file": (name, content, "text/plain")},
        )

    def share_file(
        self, identity: Identity, file_id: str, recipient: Identity
    ) -> httpx.Response:
        return self.request(
            "POST",
            f"/api/v1/files/{file_id}/share",
            identity=identity,
            json={
                "shared_with": recipient.user_id,
                "permission": "viewer",
                "shared_by": identity.user_id,
            },
        )

    def notifications(self, identity: Identity) -> httpx.Response:
        return self.get("/api/v1/notifications", identity=identity)
