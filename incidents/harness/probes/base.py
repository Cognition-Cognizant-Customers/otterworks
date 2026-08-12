"""Core types and registry for OtterWorks incident probes.

A probe reproduces one seeded incident scenario as the user experiences it:
it drives the symptom endpoint through the API gateway and reaches a verdict
from the response alone — never by reading the chaos flag.

The verdict is the verification loop. A probe that reports ``FAIL`` is a
reproduction of the incident; the same probe reporting ``PASS`` after the flag
is cleared (or the bug is fixed) is programmatic proof the incident is gone.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx


class Status(enum.StrEnum):
    #: The symptom is absent AND a legitimate request on the same path succeeds.
    PASS = "PASS"
    #: The symptom reproduced: the incident is live.
    FAIL = "FAIL"
    #: No verdict possible — backend down, precondition unmet, or the fix
    #: refuses the legitimate caller too. Never a pass.
    INCONCLUSIVE = "INCONCLUSIVE"


def unavailable(response: httpx.Response) -> bool:
    """Whether the response came from the edge rather than the scenario's handler.

    A 502/503/504 is the gateway reporting a dead backend or an open circuit
    breaker, and a 429 is the limiter. Neither is the seeded symptom (which is
    an application-level 500 or a slow 200), so nothing was assessed.
    """
    return response.status_code in (502, 503, 504, 429)


@dataclass
class Evidence:
    """The request/response pair that demonstrates the verdict."""

    request: str
    response_status: int | None = None
    response_excerpt: str = ""
    note: str = ""

    @classmethod
    def from_response(cls, response: httpx.Response, note: str = "", limit: int = 400) -> Evidence:
        return cls(
            request=f"{response.request.method} {response.request.url}",
            response_status=response.status_code,
            response_excerpt=response.text[:limit],
            note=note,
        )


@dataclass
class Result:
    """The outcome of running one incident probe."""

    scenario_id: str
    service: str
    symptom: str
    endpoint: str
    runbook: str
    status: Status
    detail: str = ""
    #: Whether a legitimate request on the same path succeeded. A verify run
    #: only passes when this is True — a fix that refuses everybody looks
    #: symptom-free but cannot pass.
    control_ok: bool | None = None
    #: Latency scenarios: what was measured and the threshold it was held to.
    measured_ms: float | None = None
    threshold_ms: float | None = None
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass
class IncidentProbe:
    """A registered incident scenario check."""

    scenario_id: str
    service: str
    symptom: str
    endpoint: str
    runbook: str
    run: Callable[..., Result]

    def result(
        self,
        status: Status,
        detail: str = "",
        *,
        control_ok: bool | None = None,
        measured_ms: float | None = None,
        threshold_ms: float | None = None,
        evidence: list[Evidence] | None = None,
    ) -> Result:
        return Result(
            scenario_id=self.scenario_id,
            service=self.service,
            symptom=self.symptom,
            endpoint=self.endpoint,
            runbook=self.runbook,
            status=status,
            detail=detail,
            control_ok=control_ok,
            measured_ms=measured_ms,
            threshold_ms=threshold_ms,
            evidence=evidence or [],
        )


REGISTRY: dict[str, IncidentProbe] = {}


def incident_probe(
    *,
    scenario_id: str,
    service: str,
    symptom: str,
    endpoint: str,
    runbook: str,
) -> Callable[[Callable[..., Result]], Callable[..., Result]]:
    """Register a scenario check under its stable scenario id."""

    def decorator(fn: Callable[..., Result]) -> Callable[..., Result]:
        if scenario_id in REGISTRY:
            raise ValueError(f"duplicate incident scenario id: {scenario_id}")
        entry = IncidentProbe(
            scenario_id=scenario_id,
            service=service,
            symptom=symptom,
            endpoint=endpoint,
            runbook=runbook,
            run=fn,
        )
        REGISTRY[scenario_id] = entry
        fn.probe = entry  # type: ignore[attr-defined]
        return fn

    return decorator
