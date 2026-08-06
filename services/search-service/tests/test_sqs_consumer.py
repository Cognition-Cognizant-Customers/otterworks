"""Tests for ``app.services.sqs_consumer`` against a stubbed SQS client.

The consumer is exercised synchronously: ``_poll_loop`` is driven by a stub
whose ``receive_message`` clears the running flag, so no thread, sleep or
wall-clock is involved.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config import MeiliSearchConfig
from app.services.indexer import Indexer
from app.services.meilisearch_client import MeiliSearchService
from app.services.sqs_consumer import SQSConsumer
from tests.fakes import FakeClient

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/search-index"


class StubSQS:
    """Minimal SQS stand-in that hands out one batch and then stops the loop."""

    def __init__(self, consumer: SQSConsumer | None = None) -> None:
        self.consumer = consumer
        self.batches: list[list[dict[str, Any]]] = []
        self.receive_kwargs: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        self.receive_kwargs.append(kwargs)
        if self.consumer is not None:
            self.consumer._running = False
        if not self.batches:
            return {}
        return {"Messages": self.batches.pop(0)}

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append(ReceiptHandle)


def message(body: Any, *, receipt: str = "rh-1", message_id: str = "m-1") -> dict[str, Any]:
    """Build an SQS message envelope; *body* may be a str or a JSON-able object."""
    return {
        "MessageId": message_id,
        "ReceiptHandle": receipt,
        "Body": body if isinstance(body, str) else json.dumps(body),
    }


def document_event(doc_id: str = "doc-1", title: str = "Quarterly report") -> dict[str, Any]:
    return {
        "event_type": "document_created",
        "payload": {"id": doc_id, "title": title, "owner_id": "user-1"},
    }


@pytest.fixture()
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture()
def search_service(fake_client: FakeClient) -> MeiliSearchService:
    with patch("app.services.meilisearch_client.meilisearch.Client") as mock_cls:
        mock_cls.return_value = fake_client
        return MeiliSearchService(
            MeiliSearchConfig(documents_index="test-docs", files_index="test-files")
        )


@pytest.fixture()
def indexer(search_service: MeiliSearchService) -> Indexer:
    return Indexer(search_service)


@pytest.fixture()
def consumer(indexer: Indexer) -> SQSConsumer:
    return SQSConsumer(indexer=indexer, queue_url=QUEUE_URL)


@pytest.fixture()
def sqs(consumer: SQSConsumer) -> StubSQS:
    return StubSQS(consumer)


def run_poll_once(consumer: SQSConsumer, sqs: StubSQS, messages: list[dict[str, Any]]) -> None:
    """Run exactly one iteration of the polling loop with *messages* delivered."""
    sqs.batches = [messages]
    consumer._running = True
    with patch.object(consumer, "_create_sqs_client", return_value=sqs):
        consumer._poll_loop()


class TestLifecycle:
    """start / stop behaviour without spawning real threads."""

    def test_start_is_skipped_without_a_queue_url(self, indexer: Indexer):
        idle = SQSConsumer(indexer=indexer, queue_url="")
        with patch("app.services.sqs_consumer.threading.Thread") as thread_cls:
            idle.start()
        thread_cls.assert_not_called()
        assert idle._running is False

    def test_start_launches_a_daemon_thread(self, consumer: SQSConsumer):
        with patch("app.services.sqs_consumer.threading.Thread") as thread_cls:
            consumer.start()
        thread_cls.assert_called_once()
        assert thread_cls.call_args.kwargs["daemon"] is True
        assert thread_cls.call_args.kwargs["name"] == "sqs-consumer"
        assert consumer._running is True

    def test_stop_joins_a_running_thread(self, consumer: SQSConsumer):
        thread = MagicMock()
        thread.is_alive.return_value = True
        consumer._thread = thread
        consumer._running = True
        consumer.stop()
        assert consumer._running is False
        thread.join.assert_called_once_with(timeout=5)

    def test_stop_is_safe_when_never_started(self, consumer: SQSConsumer):
        consumer.stop()
        assert consumer._running is False

    def test_client_is_created_without_an_endpoint_override(self, consumer: SQSConsumer):
        with patch("boto3.client") as boto_client:
            consumer._create_sqs_client()
        boto_client.assert_called_once_with("sqs", region_name="us-east-1")

    def test_client_uses_the_configured_localstack_endpoint(self, indexer: Indexer):
        local = SQSConsumer(
            indexer=indexer,
            queue_url=QUEUE_URL,
            region="eu-west-1",
            endpoint_url="http://localstack:4566",
        )
        with patch("boto3.client") as boto_client:
            local._create_sqs_client()
        boto_client.assert_called_once_with(
            "sqs", region_name="eu-west-1", endpoint_url="http://localstack:4566"
        )


class TestPollLoop:
    """Receive parameters and error handling in the loop itself."""

    def test_receive_parameters_come_from_configuration(self, consumer: SQSConsumer, sqs: StubSQS):
        run_poll_once(consumer, sqs, [])
        assert sqs.receive_kwargs == [
            {
                "QueueUrl": QUEUE_URL,
                "MaxNumberOfMessages": 10,
                "WaitTimeSeconds": 20,
                "VisibilityTimeout": 60,
            }
        ]

    @pytest.mark.parametrize("max_messages", [9, 10, 11])
    def test_max_messages_is_forwarded_verbatim_around_the_sqs_limit(
        self, indexer: Indexer, max_messages: int
    ):
        """SQS caps MaxNumberOfMessages at 10; the consumer does not validate it.

        11 is forwarded unchanged and would be rejected by SQS at runtime.
        """
        consumer = SQSConsumer(indexer=indexer, queue_url=QUEUE_URL, max_messages=max_messages)
        sqs = StubSQS(consumer)
        run_poll_once(consumer, sqs, [])
        assert sqs.receive_kwargs[0]["MaxNumberOfMessages"] == max_messages

    @pytest.mark.parametrize("wait_time", [19, 20, 21])
    def test_wait_time_is_forwarded_verbatim_around_the_sqs_limit(
        self, indexer: Indexer, wait_time: int
    ):
        consumer = SQSConsumer(
            indexer=indexer, queue_url=QUEUE_URL, wait_time_seconds=wait_time
        )
        sqs = StubSQS(consumer)
        run_poll_once(consumer, sqs, [])
        assert sqs.receive_kwargs[0]["WaitTimeSeconds"] == wait_time

    def test_receive_failure_backs_off_and_keeps_polling(self, consumer: SQSConsumer):
        calls: list[int] = []

        class FlakySQS(StubSQS):
            def receive_message(self, **kwargs: Any) -> dict[str, Any]:
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("network down")
                consumer._running = False
                return {}

        consumer._running = True
        with patch("app.services.sqs_consumer.time.sleep") as sleep, patch.object(
            consumer, "_create_sqs_client", return_value=FlakySQS()
        ):
            consumer._poll_loop()
        assert len(calls) == 2
        sleep.assert_called_once_with(5)


class TestMessageProcessing:
    """Per-message outcomes: success, poison, duplicate, unknown, failure."""

    def test_valid_document_event_is_indexed_and_deleted(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        run_poll_once(consumer, sqs, [message(document_event())])
        assert list(fake_client.index("test-docs").documents) == ["doc-1"]
        assert sqs.deleted == ["rh-1"]

    def test_sns_wrapped_event_is_unwrapped(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        envelope = {
            "Type": "Notification",
            "TopicArn": "arn:aws:sns:us-east-1:000000000000:otterworks-events",
            "Message": json.dumps(document_event("doc-sns")),
        }
        run_poll_once(consumer, sqs, [message(envelope)])
        assert list(fake_client.index("test-docs").documents) == ["doc-sns"]

    def test_duplicate_delivery_yields_one_document(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        """At-least-once delivery must not duplicate: the id is the primary key."""
        duplicate = document_event("doc-dup")
        run_poll_once(
            consumer,
            sqs,
            [
                message(duplicate, receipt="rh-a", message_id="m-a"),
                message(duplicate, receipt="rh-b", message_id="m-b"),
            ],
        )
        index = fake_client.index("test-docs")
        assert len(index.add_documents_calls) == 2
        assert list(index.documents) == ["doc-dup"]
        assert sqs.deleted == ["rh-a", "rh-b"]

    def test_redelivery_after_an_update_keeps_the_latest_version(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        run_poll_once(
            consumer,
            sqs,
            [
                message(document_event("doc-1", "First title"), receipt="rh-a"),
                message(document_event("doc-1", "Second title"), receipt="rh-b"),
            ],
        )
        documents = fake_client.index("test-docs").documents
        assert len(documents) == 1
        assert documents["doc-1"]["title"] == "Second title"

    def test_malformed_json_is_dropped_not_retried(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        """A poison message is deleted rather than routed to a DLQ (no DLQ exists)."""
        run_poll_once(consumer, sqs, [message("{not valid json", receipt="rh-bad")])
        assert fake_client.indices == {}
        assert sqs.deleted == ["rh-bad"]

    def test_event_failing_validation_is_dropped(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        """A document event with no title raises ValueError in the indexer."""
        bad = {"event_type": "document_created", "payload": {"id": "doc-1"}}
        run_poll_once(consumer, sqs, [message(bad, receipt="rh-invalid")])
        assert fake_client.index("test-docs").documents == {}
        assert sqs.deleted == ["rh-invalid"]

    def test_unknown_event_type_is_acknowledged_without_indexing(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        unknown = {"event_type": "document_archived", "payload": {"id": "doc-1"}}
        run_poll_once(consumer, sqs, [message(unknown, receipt="rh-unknown")])
        assert fake_client.index("test-docs").documents == {}
        assert sqs.deleted == ["rh-unknown"]

    def test_unroutable_camel_case_event_is_acknowledged(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        """``file_shared`` carries no file metadata, so it is skipped."""
        run_poll_once(
            consumer,
            sqs,
            [message({"eventType": "file_shared", "fileId": "f-1"}, receipt="rh-shared")],
        )
        assert fake_client.indices == {}
        assert sqs.deleted == ["rh-shared"]

    def test_indexing_failure_leaves_the_message_on_the_queue(
        self, consumer: SQSConsumer, sqs: StubSQS
    ):
        """An unexpected backend error must not ack the message."""
        with patch.object(
            consumer.indexer, "process_event", side_effect=RuntimeError("meili down")
        ):
            run_poll_once(consumer, sqs, [message(document_event(), receipt="rh-fail")])
        assert sqs.deleted == []

    def test_message_without_a_receipt_handle_is_still_processed(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        raw = {"MessageId": "m-1", "Body": json.dumps(document_event("doc-no-rh"))}
        run_poll_once(consumer, sqs, [raw])
        assert list(fake_client.index("test-docs").documents) == ["doc-no-rh"]
        assert sqs.deleted == [""]

    def test_empty_batch_touches_nothing(
        self, consumer: SQSConsumer, sqs: StubSQS, fake_client: FakeClient
    ):
        run_poll_once(consumer, sqs, [])
        assert fake_client.indices == {}
        assert sqs.deleted == []


class TestNormalizeEvent:
    """``_normalize_event`` maps the producers' payload shapes onto the indexer's."""

    @pytest.mark.parametrize(
        ("event_type", "action"),
        [
            ("document_created", "index_document"),
            ("document_updated", "index_document"),
            ("document_deleted", "delete"),
            ("file_created", "index_file"),
            ("file_uploaded", "index_file"),
            ("file_updated", "index_file"),
            ("file_deleted", "delete"),
            ("file_trashed", "delete"),
            ("file_restored", "index_file"),
        ],
    )
    def test_snake_case_events_map_to_indexer_actions(self, event_type: str, action: str):
        normalized = SQSConsumer._normalize_event(
            {"event_type": event_type, "payload": {"id": "x"}}
        )
        assert normalized == {"action": action, "data": {"id": "x"}}

    def test_unmapped_snake_case_event_type_passes_through_as_the_action(self):
        normalized = SQSConsumer._normalize_event(
            {"event_type": "document_archived", "payload": {"id": "x"}}
        )
        assert normalized["action"] == "document_archived"

    def test_camel_case_upload_event_is_translated_to_snake_case(self):
        normalized = SQSConsumer._normalize_event(
            {
                "eventType": "file_uploaded",
                "fileId": "f-1",
                "name": "notes.txt",
                "mimeType": "text/plain",
                "ownerId": "user-1",
                "folderId": "folder-1",
                "sizeBytes": 42,
                "tags": ["a"],
                "timestamp": "2026-01-01T00:00:00Z",
            }
        )
        assert normalized == {
            "action": "index_file",
            "data": {
                "id": "f-1",
                "name": "notes.txt",
                "mime_type": "text/plain",
                "owner_id": "user-1",
                "folder_id": "folder-1",
                "size": 42,
                "tags": ["a"],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        }

    def test_camel_case_upload_event_defaults_absent_fields(self):
        normalized = SQSConsumer._normalize_event({"eventType": "file_uploaded"})
        assert normalized["data"]["id"] == ""
        assert normalized["data"]["size"] == 0
        assert normalized["data"]["tags"] == []
        assert normalized["data"]["created_at"] is None

    def test_camel_case_delete_event_carries_only_the_identifier(self):
        normalized = SQSConsumer._normalize_event(
            {"eventType": "file_trashed", "fileId": "f-2", "name": "ignored.txt"}
        )
        assert normalized == {"action": "delete", "data": {"type": "file", "id": "f-2"}}

    @pytest.mark.parametrize("event_type", ["file_shared", "file_moved", "totally_unknown"])
    def test_unroutable_camel_case_events_are_returned_unchanged(self, event_type: str):
        body = {"eventType": event_type, "fileId": "f-3"}
        assert SQSConsumer._normalize_event(body) == body

    def test_native_indexer_format_is_passed_through(self):
        body = {"action": "index_document", "data": {"id": "doc-1", "title": "t"}}
        assert SQSConsumer._normalize_event(body) == body

    def test_event_type_without_a_payload_is_not_treated_as_snake_case(self):
        body = {"event_type": "document_created", "id": "doc-1"}
        assert SQSConsumer._normalize_event(body) == body
