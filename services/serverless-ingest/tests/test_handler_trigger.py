"""Unit tests for the ingest trigger Lambda.

The Step Functions client is stubbed — nothing here touches AWS or the network.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import handler_trigger  # noqa: E402

STATE_MACHINE_ARN = (
    "arn:aws:states:us-east-1:599083837640:stateMachine:ow-tp-custbill-pipeline"
)
BUCKET = "ow-tp-ingest-599083837640"


class ExecutionAlreadyExists(Exception):
    """Stand-in for botocore's generated sfn.exceptions.ExecutionAlreadyExists."""


class StubExceptions:
    ExecutionAlreadyExists = ExecutionAlreadyExists


class StubStepFunctions:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.error = error
        self.exceptions = StubExceptions()

    def start_execution(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"executionArn": f"{STATE_MACHINE_ARN}:{kwargs['name']}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("STATE_MACHINE_ARN", STATE_MACHINE_ARN)
    monkeypatch.setenv("BUCKET", BUCKET)


@pytest.fixture
def sfn(monkeypatch):
    stub = StubStepFunctions()
    monkeypatch.setattr(handler_trigger, "_client", lambda: stub)
    return stub


def eventbridge_body(key: str, bucket: str = BUCKET, time: str = "2026-08-15T12:00:00Z"):
    return json.dumps(
        {
            "version": "0",
            "id": "d1b1a5b0-0000-4000-8000-000000000000",
            "detail-type": "Object Created",
            "source": "aws.s3",
            "account": "599083837640",
            "time": time,
            "region": "us-east-1",
            "detail": {
                "bucket": {"name": bucket},
                "object": {"key": key, "size": 4300},
                "reason": "PutObject",
            },
        }
    )


def sqs_event(*bodies: str):
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": body} for i, body in enumerate(bodies)
        ]
    }


def test_happy_path_starts_one_execution_per_file(sfn):
    key = "landing/demo/CUSTBILL_DEMO_001.dat"
    result = handler_trigger.handler(sqs_event(eventbridge_body(key)), None)

    assert result == {"batchItemFailures": []}
    assert len(sfn.calls) == 1
    call = sfn.calls[0]
    assert call["stateMachineArn"] == STATE_MACHINE_ARN
    assert json.loads(call["input"]) == {
        "ns": "demo",
        "bucket": BUCKET,
        "key": key,
        "filename": "CUSTBILL_DEMO_001.dat",
    }


def test_execution_name_is_deterministic_sanitised_and_bounded(sfn):
    key = "landing/demo/CUSTBILL_DEMO_001.dat"
    event = sqs_event(eventbridge_body(key))

    handler_trigger.handler(event, None)
    handler_trigger.handler(event, None)

    names = [call["name"] for call in sfn.calls]
    assert names[0] == names[1], "same file + event time must give the same name"
    name = names[0]
    assert name.startswith("demo-CUSTBILL_DEMO_001-")
    assert len(name) <= 80
    assert all(c.isalnum() or c in "_-" for c in name)


def test_different_event_time_gives_a_different_execution_name(sfn):
    key = "landing/demo/CUSTBILL_DEMO_001.dat"
    handler_trigger.handler(sqs_event(eventbridge_body(key)), None)
    handler_trigger.handler(
        sqs_event(eventbridge_body(key, time="2026-08-15T13:30:00Z")), None
    )

    assert sfn.calls[0]["name"] != sfn.calls[1]["name"]


@pytest.mark.parametrize(
    "key",
    [
        "landing/demo/NOTCUSTBILL_DEMO_001.dat",
        "landing/demo/CUSTBILL_DEMO_001.txt",
        "parsed/demo/CUSTBILL_DEMO_001.psv",
        "landing/CUSTBILL_DEMO_001.dat",
    ],
)
def test_non_custbill_keys_are_skipped_as_success(sfn, key):
    result = handler_trigger.handler(sqs_event(eventbridge_body(key)), None)

    assert result == {"batchItemFailures": []}
    assert sfn.calls == []


def test_malformed_body_becomes_a_batch_item_failure(sfn):
    event = {
        "Records": [
            {"messageId": "bad-1", "body": "this is not json"},
            {
                "messageId": "good-1",
                "body": eventbridge_body("landing/demo/CUSTBILL_DEMO_002.dat"),
            },
        ]
    }

    result = handler_trigger.handler(event, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "bad-1"}]}
    assert len(sfn.calls) == 1, "healthy records in the batch still run"


def test_missing_detail_fields_become_a_batch_item_failure(sfn):
    body = json.dumps({"detail-type": "Object Created", "detail": {"bucket": {}}})

    result = handler_trigger.handler(sqs_event(body), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert sfn.calls == []


def test_execution_already_exists_is_swallowed(monkeypatch):
    stub = StubStepFunctions(error=ExecutionAlreadyExists("already there"))
    monkeypatch.setattr(handler_trigger, "_client", lambda: stub)

    result = handler_trigger.handler(
        sqs_event(eventbridge_body("landing/demo/CUSTBILL_DEMO_001.dat")), None
    )

    assert result == {"batchItemFailures": []}
    assert len(stub.calls) == 1


def test_other_step_functions_errors_are_retried(monkeypatch):
    stub = StubStepFunctions(error=RuntimeError("throttled"))
    monkeypatch.setattr(handler_trigger, "_client", lambda: stub)

    result = handler_trigger.handler(
        sqs_event(eventbridge_body("landing/demo/CUSTBILL_DEMO_001.dat")), None
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}


def test_missing_state_machine_arn_env_is_a_batch_item_failure(monkeypatch, sfn):
    monkeypatch.delenv("STATE_MACHINE_ARN", raising=False)
    assert "STATE_MACHINE_ARN" not in os.environ

    result = handler_trigger.handler(
        sqs_event(eventbridge_body("landing/demo/CUSTBILL_DEMO_001.dat")), None
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
