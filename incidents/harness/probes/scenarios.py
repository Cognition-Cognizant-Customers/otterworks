"""The four seeded incident scenarios, asserted through the API gateway.

Each check reproduces the user-visible symptom and, on the same path, proves
that a legitimate request still succeeds. A backend the gateway reports as
down (502/503/504) assessed nothing and is INCONCLUSIVE, never a pass.
"""

from __future__ import annotations

import os
import statistics
import time

import httpx

from .base import Evidence, Result, Status, incident_probe, unavailable
from .context import IncidentContext

#: Chaos injects 3-5s per document read; a healthy read is tens of ms. The
#: threshold sits well above healthy jitter and well below the injected floor.
LATENCY_THRESHOLD_MS = float(os.getenv("INCIDENT_LATENCY_THRESHOLD_MS", "2500"))
LATENCY_SAMPLES = int(os.getenv("INCIDENT_LATENCY_SAMPLES", "3"))

#: Notifications are delivered asynchronously (SNS -> SQS -> consumer), so the
#: inbox is polled: long enough for a healthy pipeline, short enough that an
#: event the strict parser dropped is a verdict rather than a hang.
NOTIFY_TIMEOUT_S = float(os.getenv("INCIDENT_NOTIFY_TIMEOUT", "60"))
NOTIFY_POLL_S = 3.0


@incident_probe(
    scenario_id="search-service:suggest_500",
    service="search-service",
    symptom="autocomplete suggestions return HTTP 500 instead of a suggestion list",
    endpoint="GET /api/v1/search/suggest?q=<prefix>",
    runbook="docs/runbooks/search-suggest-500.md",
)
def search_suggest_500(ctx: IncidentContext) -> Result:
    self = search_suggest_500.probe
    try:
        response = ctx.get(
            "/api/v1/search/suggest", params={"q": "ot"}, identity=ctx.reporter
        )
    except httpx.HTTPError as exc:
        return self.result(Status.INCONCLUSIVE, f"suggest endpoint unreachable: {exc}")
    evidence = [Evidence.from_response(response, note="autocomplete request through the gateway")]
    if unavailable(response):
        return self.result(
            Status.INCONCLUSIVE,
            f"the gateway reported the backend unavailable ({response.status_code}); "
            "nothing about the suggest handler was assessed",
            evidence=evidence,
        )
    if response.status_code >= 500:
        return self.result(
            Status.FAIL,
            f"suggest returned {response.status_code}: the symptom reproduces",
            control_ok=False,
            evidence=evidence,
        )
    if response.status_code != 200:
        return self.result(
            Status.INCONCLUSIVE,
            f"suggest refused the legitimate caller ({response.status_code}); the symptom "
            "is absent but a fix that refuses everybody cannot pass",
            control_ok=False,
            evidence=evidence,
        )
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or "suggestions" not in body:
        return self.result(
            Status.INCONCLUSIVE,
            "suggest returned 200 but not a suggestion list; the legitimate request "
            "did not demonstrably succeed",
            control_ok=False,
            evidence=evidence,
        )
    return self.result(
        Status.PASS,
        "suggest returned 200 with a suggestion list for a legitimate caller",
        control_ok=True,
        evidence=evidence,
    )


@incident_probe(
    scenario_id="file-service:upload_s3_error",
    service="file-service",
    symptom="file uploads return HTTP 500 (S3 NoSuchBucket on a misconfigured bucket)",
    endpoint="POST /api/v1/files/upload",
    runbook="docs/runbooks/file-upload-failure.md",
)
def file_upload_s3_error(ctx: IncidentContext) -> Result:
    self = file_upload_s3_error.probe
    try:
        response = ctx.upload_file(
            ctx.reporter, f"incident-{ctx.run_id}.txt", b"incident harness upload"
        )
    except httpx.HTTPError as exc:
        return self.result(Status.INCONCLUSIVE, f"upload endpoint unreachable: {exc}")
    evidence = [Evidence.from_response(response, note="multipart upload through the gateway")]
    if unavailable(response):
        return self.result(
            Status.INCONCLUSIVE,
            f"the gateway reported the backend unavailable ({response.status_code}); "
            "nothing about the upload path was assessed",
            evidence=evidence,
        )
    if response.status_code >= 500:
        return self.result(
            Status.FAIL,
            f"upload returned {response.status_code}: the symptom reproduces",
            control_ok=False,
            evidence=evidence,
        )
    if response.status_code not in (200, 201):
        return self.result(
            Status.INCONCLUSIVE,
            f"upload refused the legitimate caller ({response.status_code}); the symptom "
            "is absent but a fix that refuses everybody cannot pass",
            control_ok=False,
            evidence=evidence,
        )
    return self.result(
        Status.PASS,
        "a legitimate upload succeeded",
        control_ok=True,
        evidence=evidence,
    )


@incident_probe(
    scenario_id="document-service:slow_queries",
    service="document-service",
    symptom=f"document reads exceed the {LATENCY_THRESHOLD_MS:.0f}ms latency threshold",
    endpoint="GET /api/v1/documents/",
    runbook="docs/runbooks/document-service-slow.md",
)
def document_slow_queries(ctx: IncidentContext) -> Result:
    self = document_slow_queries.probe
    samples_ms: list[float] = []
    last: httpx.Response | None = None
    for _ in range(LATENCY_SAMPLES):
        started = time.monotonic()
        try:
            last = ctx.get("/api/v1/documents/", identity=ctx.reporter)
        except httpx.HTTPError as exc:
            return self.result(
                Status.INCONCLUSIVE,
                f"documents endpoint unreachable: {exc}",
                threshold_ms=LATENCY_THRESHOLD_MS,
            )
        samples_ms.append((time.monotonic() - started) * 1000.0)
    measured = statistics.median(samples_ms)
    evidence = [
        Evidence.from_response(
            last,
            note=(
                f"median of {LATENCY_SAMPLES} timed reads: {measured:.0f}ms "
                f"(samples: {', '.join(f'{s:.0f}ms' for s in samples_ms)})"
            ),
        )
    ]
    if unavailable(last):
        return self.result(
            Status.INCONCLUSIVE,
            f"the gateway reported the backend unavailable ({last.status_code}); "
            "a dead backend is not a latency measurement",
            measured_ms=measured,
            threshold_ms=LATENCY_THRESHOLD_MS,
            evidence=evidence,
        )
    if last.status_code != 200:
        return self.result(
            Status.INCONCLUSIVE,
            f"document reads returned {last.status_code}; latency of a refused request "
            "says nothing about the query path",
            control_ok=False,
            measured_ms=measured,
            threshold_ms=LATENCY_THRESHOLD_MS,
            evidence=evidence,
        )
    if measured >= LATENCY_THRESHOLD_MS:
        return self.result(
            Status.FAIL,
            f"median read latency {measured:.0f}ms breaches the "
            f"{LATENCY_THRESHOLD_MS:.0f}ms threshold: the symptom reproduces",
            control_ok=True,
            measured_ms=measured,
            threshold_ms=LATENCY_THRESHOLD_MS,
            evidence=evidence,
        )
    return self.result(
        Status.PASS,
        f"median read latency {measured:.0f}ms is under the "
        f"{LATENCY_THRESHOLD_MS:.0f}ms threshold and the read succeeded",
        control_ok=True,
        measured_ms=measured,
        threshold_ms=LATENCY_THRESHOLD_MS,
        evidence=evidence,
    )


@incident_probe(
    scenario_id="notification-service:consumer_strict_schema",
    service="notification-service",
    symptom="a file share never produces the recipient's notification (events rejected)",
    endpoint="POST /api/v1/files/{id}/share -> GET /api/v1/notifications",
    runbook="docs/runbooks/notification-processing-failure.md",
)
def notification_strict_schema(ctx: IncidentContext) -> Result:
    self = notification_strict_schema.probe

    # Control first: is the inbox reachable at all? A dead notification
    # backend would otherwise be indistinguishable from a dropped event.
    try:
        inbox = ctx.notifications(ctx.recipient)
    except httpx.HTTPError as exc:
        return self.result(Status.INCONCLUSIVE, f"notifications endpoint unreachable: {exc}")
    if inbox.status_code != 200:
        return self.result(
            Status.INCONCLUSIVE,
            f"the recipient's inbox returned {inbox.status_code}; a backend that is "
            "down cannot prove an event was or was not delivered",
            control_ok=False,
            evidence=[Evidence.from_response(inbox, note="inbox reachability control")],
        )

    # Stage the event: upload a file and share it with the recipient. Both are
    # preconditions, not the symptom under test.
    try:
        upload = ctx.upload_file(
            ctx.reporter, f"incident-share-{ctx.run_id}.txt", b"incident share fixture"
        )
    except httpx.HTTPError as exc:
        return self.result(Status.INCONCLUSIVE, f"could not stage a file to share: {exc}")
    if upload.status_code not in (200, 201):
        return self.result(
            Status.INCONCLUSIVE,
            f"could not stage a file to share (upload returned {upload.status_code}); "
            "the share event was never emitted",
            evidence=[Evidence.from_response(upload, note="staging upload")],
        )
    try:
        file_id = str(upload.json().get("file", {}).get("id", ""))
    except ValueError:
        file_id = ""
    if not file_id:
        return self.result(
            Status.INCONCLUSIVE,
            "the staging upload returned no file id; the share event was never emitted",
        )
    try:
        share = ctx.share_file(ctx.reporter, file_id, ctx.recipient)
    except httpx.HTTPError as exc:
        return self.result(Status.INCONCLUSIVE, f"could not share the staged file: {exc}")
    if share.status_code not in (200, 201):
        return self.result(
            Status.INCONCLUSIVE,
            f"the share request returned {share.status_code}; the share event was "
            "never emitted",
            evidence=[Evidence.from_response(share, note="staging share")],
        )

    # The symptom: the consumer drops the event, so the notification never
    # lands in the recipient's inbox.
    deadline = time.monotonic() + NOTIFY_TIMEOUT_S
    last: httpx.Response | None = None
    while time.monotonic() < deadline:
        try:
            last = ctx.notifications(ctx.recipient)
        except httpx.HTTPError:
            last = None
        if last is not None and last.status_code == 200:
            try:
                items = last.json().get("data", [])
            except ValueError:
                items = []
            for item in items:
                if isinstance(item, dict) and item.get("resourceId") == file_id:
                    return self.result(
                        Status.PASS,
                        "the shared-file notification reached the recipient "
                        f"within {NOTIFY_TIMEOUT_S:.0f}s",
                        control_ok=True,
                        evidence=[
                            Evidence.from_response(
                                last, note=f"notification for file {file_id} delivered"
                            )
                        ],
                    )
        time.sleep(NOTIFY_POLL_S)
    if last is None or last.status_code != 200:
        return self.result(
            Status.INCONCLUSIVE,
            "the inbox stopped answering while waiting for the notification; a backend "
            "that is down is not a verdict",
            control_ok=False,
        )
    return self.result(
        Status.FAIL,
        f"the share event produced no notification within {NOTIFY_TIMEOUT_S:.0f}s "
        "even though the inbox itself answers: the consumer is dropping events",
        control_ok=True,
        evidence=[
            Evidence.from_response(
                last, note=f"no notification for file {file_id} after {NOTIFY_TIMEOUT_S:.0f}s"
            )
        ],
    )
