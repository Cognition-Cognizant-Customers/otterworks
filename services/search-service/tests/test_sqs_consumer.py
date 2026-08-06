"""WP-07: SQS consumer — event normalization, acking, and failure handling.

boto3 is never imported for real work here: ``_create_sqs_client`` is patched or
the fake client is handed straight to ``_process_message``. The ack log on the
fake (``deleted``) is the assertion surface: a message that is *not* acked stays
on the queue and is eventually redriven to the DLQ.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.indexer import Indexer
from app.services.sqs_consumer import SQSConsumer
from tests.conftest_wp07 import DOCUMENTS_INDEX, FILES_INDEX, build_service
from tests.fakes import FakeMeiliClient, FakeSQSClient, sqs_message

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/search-indexing"


@pytest.fixture()
def meili() -> FakeMeiliClient:
    return FakeMeiliClient()


@pytest.fixture()
def indexer(meili: FakeMeiliClient) -> Indexer:
    return Indexer(build_service(meili))


@pytest.fixture()
def consumer(indexer: Indexer) -> SQSConsumer:
    return SQSConsumer(
        indexer=indexer,
        queue_url=QUEUE_URL,
        region="us-east-1",
        max_messages=10,
        wait_time_seconds=20,
        visibility_timeout=60,
    )


@pytest.fixture()
def sqs() -> FakeSQSClient:
    return FakeSQSClient()


DOCUMENT_EVENT = {
    "event_type": "document_created",
    "payload": {"id": "doc-1", "title": "Quarterly report", "owner_id": "user-1"},
}
FILE_EVENT = {
    "eventType": "file_uploaded",
    "fileId": "file-1",
    "name": "a.pdf",
    "mimeType": "application/pdf",
    "ownerId": "user-1",
    "sizeBytes": 2048,
    "timestamp": "2024-01-01T00:00:00Z",
}


class TestEventNormalization:
    def test_snake_case_event_with_payload(self, consumer):
        assert SQSConsumer._normalize_event(DOCUMENT_EVENT) == {
            "action": "index_document",
            "data": DOCUMENT_EVENT["payload"],
        }

    def test_camel_case_file_event_is_mapped_to_snake_case(self, consumer):
        normalized = SQSConsumer._normalize_event(FILE_EVENT)
        assert normalized["action"] == "index_file"
        assert normalized["data"]["id"] == "file-1"
        assert normalized["data"]["mime_type"] == "application/pdf"
        assert normalized["data"]["size"] == 2048

    def test_camel_case_delete_event_carries_only_the_id(self, consumer):
        normalized = SQSConsumer._normalize_event(
            {"eventType": "file_deleted", "fileId": "file-9"}
        )
        assert normalized == {"action": "delete", "data": {"type": "file", "id": "file-9"}}

    def test_events_without_file_metadata_are_passed_through_unmapped(self, consumer):
        """file_shared/file_moved carry no metadata, so they are not indexable."""
        event = {"eventType": "file_shared", "fileId": "file-1"}
        assert SQSConsumer._normalize_event(event) == event

    def test_unknown_snake_case_event_type_keeps_its_action(self, consumer):
        normalized = SQSConsumer._normalize_event(
            {"event_type": "document_archived", "payload": {"id": "doc-1"}}
        )
        assert normalized["action"] == "document_archived"

    def test_already_normalized_event_passes_through(self, consumer):
        event = {"action": "index_document", "data": {"id": "doc-1", "title": "T"}}
        assert SQSConsumer._normalize_event(event) == event


class TestMessageProcessing:
    def test_well_formed_message_is_indexed_and_acked(self, consumer, sqs, meili):
        consumer._process_message(sqs, sqs_message(DOCUMENT_EVENT))
        assert list(meili.index(DOCUMENTS_INDEX).documents) == ["doc-1"]
        assert sqs.deleted == ["receipt-1"]

    def test_file_event_is_indexed_into_the_files_index(self, consumer, sqs, meili):
        consumer._process_message(sqs, sqs_message(FILE_EVENT))
        assert list(meili.index(FILES_INDEX).documents) == ["file-1"]
        assert sqs.deleted == ["receipt-1"]

    def test_sns_wrapped_message_is_unwrapped(self, consumer, sqs, meili):
        envelope = {
            "Type": "Notification",
            "TopicArn": "arn:aws:sns:us-east-1:000000000000:search-events",
            "Message": json.dumps(DOCUMENT_EVENT),
        }
        consumer._process_message(sqs, sqs_message(envelope))
        assert list(meili.index(DOCUMENTS_INDEX).documents) == ["doc-1"]

    def test_delete_event_removes_the_document_and_acks(self, consumer, sqs, meili):
        consumer._process_message(sqs, sqs_message(DOCUMENT_EVENT))
        consumer._process_message(
            sqs,
            sqs_message(
                {"event_type": "document_deleted", "payload": {"id": "doc-1", "type": "document"}},
                receipt_handle="receipt-2",
            ),
        )
        assert meili.index(DOCUMENTS_INDEX).documents == {}
        assert sqs.deleted == ["receipt-1", "receipt-2"]

    def test_duplicate_delivery_is_idempotent(self, consumer, sqs, meili):
        """At-least-once delivery: the same event twice -> one indexed entry."""
        message = sqs_message(DOCUMENT_EVENT)
        consumer._process_message(sqs, message)
        consumer._process_message(sqs, message)
        assert len(meili.index(DOCUMENTS_INDEX).documents) == 1
        assert sqs.deleted == ["receipt-1", "receipt-1"]

    def test_unknown_event_type_is_acked_without_indexing(self, consumer, sqs, meili):
        consumer._process_message(
            sqs, sqs_message({"action": "launch_rockets", "data": {}})
        )
        assert meili.index(DOCUMENTS_INDEX).documents == {}
        assert sqs.deleted == ["receipt-1"]

    def test_empty_body_is_acked_without_indexing(self, consumer, sqs, meili):
        consumer._process_message(sqs, sqs_message("{}"))
        assert meili.index(DOCUMENTS_INDEX).documents == {}
        assert sqs.deleted == ["receipt-1"]

    def test_missing_receipt_handle_does_not_crash_the_consumer(self, consumer, sqs):
        message = sqs_message(DOCUMENT_EVENT)
        del message["ReceiptHandle"]
        consumer._process_message(sqs, message)
        assert sqs.deleted == [""]


class TestPoisonMessageHandling:
    """Malformed / invalid messages: what is acked and what is retried."""

    def test_malformed_json_is_deleted_rather_than_left_for_the_dlq(
        self, consumer, sqs, meili
    ):
        """Current behaviour (see FINDING 3): the poison message is acked."""
        consumer._process_message(sqs, sqs_message("{not json at all"))
        assert meili.index(DOCUMENTS_INDEX).documents == {}
        assert sqs.deleted == ["receipt-1"]

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "FINDING 3: app/services/sqs_consumer.py:_process_message deletes a "
            "message whose body is not valid JSON. The queue's redrive policy "
            "can therefore never move it to the DLQ and the payload is lost "
            "with only a log line."
        ),
    )
    def test_malformed_json_should_be_left_on_the_queue_for_the_dlq(self, consumer, sqs):
        consumer._process_message(sqs, sqs_message("{not json at all"))
        assert sqs.deleted == []

    def test_message_missing_a_required_field_is_deleted(self, consumer, sqs, meili):
        """A document event with no title raises ValueError -> acked (FINDING 4)."""
        consumer._process_message(
            sqs,
            sqs_message({"event_type": "document_created", "payload": {"id": "doc-1"}}),
        )
        assert meili.index(DOCUMENTS_INDEX).documents == {}
        assert sqs.deleted == ["receipt-1"]

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "FINDING 4: a schema-invalid event (missing required field) is "
            "acked by the `except ValueError` branch, so invalid payloads never "
            "reach the DLQ and are silently dropped."
        ),
    )
    def test_message_missing_a_required_field_should_reach_the_dlq(self, consumer, sqs):
        consumer._process_message(
            sqs,
            sqs_message({"event_type": "document_created", "payload": {"id": "doc-1"}}),
        )
        assert sqs.deleted == []

    def test_file_event_missing_its_id_is_deleted(self, consumer, sqs, meili):
        consumer._process_message(
            sqs, sqs_message({"eventType": "file_uploaded", "name": "a.pdf"})
        )
        assert meili.index(FILES_INDEX).documents == {}
        assert sqs.deleted == ["receipt-1"]

    def test_body_that_is_valid_json_but_not_an_object_is_not_acked(
        self, consumer, sqs
    ):
        """``"[]"`` parses but has no ``.get``: an AttributeError leaves it queued."""
        consumer._process_message(sqs, sqs_message("[]"))
        assert sqs.deleted == []

    def test_handler_exception_leaves_the_message_for_the_visibility_timeout(
        self, consumer, sqs
    ):
        """A transient indexer failure must not ack — the message must reappear."""
        consumer.indexer = MagicMock()
        consumer.indexer.process_event.side_effect = RuntimeError("meilisearch down")
        consumer._process_message(sqs, sqs_message(DOCUMENT_EVENT))
        assert sqs.deleted == []

    def test_handler_exception_does_not_shorten_the_visibility_timeout(
        self, consumer, sqs
    ):
        """The consumer relies purely on the queue's VisibilityTimeout."""
        consumer.indexer = MagicMock()
        consumer.indexer.process_event.side_effect = RuntimeError("boom")
        consumer._process_message(sqs, sqs_message(DOCUMENT_EVENT))
        assert sqs.changed_visibility == []


class TestLifecycle:
    def test_start_is_a_no_op_without_a_queue_url(self, indexer):
        consumer = SQSConsumer(indexer=indexer, queue_url="")
        consumer.start()
        assert consumer._thread is None
        assert consumer._running is False

    def test_start_then_stop_joins_the_polling_thread(self, consumer):
        with patch.object(consumer, "_create_sqs_client", return_value=FakeSQSClient()):
            consumer.start()
            assert consumer._thread is not None
            consumer.stop()
        assert consumer._running is False
        assert not consumer._thread.is_alive()

    def test_stop_before_start_is_safe(self, consumer):
        consumer.stop()
        assert consumer._running is False

    def test_poll_loop_passes_the_configured_receive_parameters(self, consumer):
        sqs = FakeSQSClient(batches=[[sqs_message(DOCUMENT_EVENT)]])

        def stop_after_first(**kwargs):
            consumer._running = False
            return {"Messages": []}

        sqs.receive_message = MagicMock(side_effect=stop_after_first)
        consumer._running = True
        with patch.object(consumer, "_create_sqs_client", return_value=sqs):
            consumer._poll_loop()

        sqs.receive_message.assert_called_once_with(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
            VisibilityTimeout=60,
        )

    def test_poll_loop_processes_a_received_batch(self, consumer, meili):
        sqs = FakeSQSClient(batches=[[sqs_message(DOCUMENT_EVENT)]])
        original_receive = sqs.receive_message

        def receive_then_stop(**kwargs):
            result = original_receive(**kwargs)
            consumer._running = False
            return result

        sqs.receive_message = receive_then_stop
        consumer._running = True
        with patch.object(consumer, "_create_sqs_client", return_value=sqs):
            consumer._poll_loop()

        assert list(meili.index(DOCUMENTS_INDEX).documents) == ["doc-1"]
        assert sqs.deleted == ["receipt-1"]

    def test_poll_loop_backs_off_and_keeps_running_after_a_receive_error(
        self, consumer
    ):
        """A receive failure sleeps (mocked) and retries rather than dying."""
        sqs = FakeSQSClient()
        attempts = {"n": 0}

        def failing_receive(**kwargs):
            attempts["n"] += 1
            raise RuntimeError("network partition")

        sqs.receive_message = failing_receive

        def stop_sleeping(_seconds):
            consumer._running = False

        consumer._running = True
        with patch.object(consumer, "_create_sqs_client", return_value=sqs), patch(
            "app.services.sqs_consumer.time.sleep", side_effect=stop_sleeping
        ) as sleep_mock:
            consumer._poll_loop()

        assert attempts["n"] == 1
        sleep_mock.assert_called_once_with(5)

    def test_client_is_created_without_an_endpoint_override_by_default(self, consumer):
        with patch("boto3.client") as boto_client:
            consumer._create_sqs_client()
        boto_client.assert_called_once_with("sqs", region_name="us-east-1")

    def test_localstack_endpoint_override_is_honoured(self, indexer):
        consumer = SQSConsumer(
            indexer=indexer,
            queue_url=QUEUE_URL,
            region="eu-west-1",
            endpoint_url="http://localstack:4566",
        )
        with patch("boto3.client") as boto_client:
            consumer._create_sqs_client()
        boto_client.assert_called_once_with(
            "sqs", region_name="eu-west-1", endpoint_url="http://localstack:4566"
        )
