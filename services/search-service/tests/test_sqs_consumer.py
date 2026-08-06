"""Tests for the SQS consumer.

The consumer is driven through its public ``start``/``stop`` API with a fake
boto3 SQS client, so the assertions describe observable behaviour: which
messages get acknowledged (deleted), which reach the indexer, and that the
background thread is gone once ``stop`` returns.
"""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.indexer import Indexer
from app.services.sqs_consumer import SQSConsumer

QUEUE_URL = "http://localhost:4566/000000000000/search-indexing"
CONSUMER_THREAD_NAME = "sqs-consumer"
DRAIN_TIMEOUT_SECONDS = 10


class FakeSQSClient:
    """Minimal in-memory stand-in for a boto3 SQS client.

    Hands out the queued batches one ``receive_message`` call at a time and
    signals ``drained`` once every batch has been delivered and processed.
    """

    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = list(batches)
        self._lock = threading.Lock()
        self.deleted_receipts: list[str] = []
        self.drained = threading.Event()

    def receive_message(self, **_kwargs: Any) -> dict[str, Any]:
        with self._lock:
            if self._batches:
                return {"Messages": self._batches.pop(0)}
        self.drained.set()
        return {}

    # Argument names mirror the boto3 SQS API, which uses PascalCase kwargs.
    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> dict[str, Any]:
        with self._lock:
            self.deleted_receipts.append(ReceiptHandle)
        return {}


def _message(receipt: str, body: str, message_id: str = "msg-1") -> dict[str, Any]:
    return {"MessageId": message_id, "ReceiptHandle": receipt, "Body": body}


def _consumer_threads_alive() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == CONSUMER_THREAD_NAME and t.is_alive()]


@pytest.fixture()
def indexer(meilisearch_service) -> Indexer:
    """A real Indexer backed by a mocked MeiliSearch client."""
    return Indexer(meilisearch_service)


@pytest.fixture()
def run_consumer():
    """Run a consumer against a fake SQS client until its batches are drained."""
    started: list[SQSConsumer] = []

    def _run(indexer: Indexer | MagicMock, batches: list[list[dict[str, Any]]]) -> FakeSQSClient:
        fake_sqs = FakeSQSClient(batches)
        consumer = SQSConsumer(indexer=indexer, queue_url=QUEUE_URL, wait_time_seconds=0)
        started.append(consumer)
        with patch("boto3.client", return_value=fake_sqs):
            consumer.start()
            assert fake_sqs.drained.wait(timeout=DRAIN_TIMEOUT_SECONDS), "consumer never polled the queue"
            consumer.stop()
        return fake_sqs

    yield _run

    for consumer in started:
        consumer.stop()


class TestSQSConsumerMessageHandling:
    """Tests for how individual messages are handled."""

    def test_consumer_malformed_json_body_acknowledges_without_indexing(self, run_consumer):
        """A message whose body is not JSON is dropped from the queue, not retried forever."""
        indexer = MagicMock(spec=Indexer)
        fake_sqs = run_consumer(indexer, [[_message("receipt-malformed", "{not-json")]])

        assert fake_sqs.deleted_receipts == ["receipt-malformed"]
        indexer.process_event.assert_not_called()

    def test_consumer_message_missing_required_field_acknowledges_message(self, run_consumer, indexer):
        """An event whose payload fails indexer validation is acknowledged, not requeued."""
        body = json.dumps({"event_type": "document_created", "payload": {"title": "No ID"}})
        fake_sqs = run_consumer(indexer, [[_message("receipt-invalid", body)]])

        assert fake_sqs.deleted_receipts == ["receipt-invalid"]

    def test_consumer_valid_document_event_indexes_and_acknowledges(self, run_consumer, indexer, mock_meilisearch_client):
        """A well-formed document event reaches MeiliSearch and is acknowledged."""
        body = json.dumps(
            {
                "event_type": "document_created",
                "payload": {"id": "doc-1", "title": "Quarterly report"},
            }
        )
        fake_sqs = run_consumer(indexer, [[_message("receipt-valid", body)]])

        assert fake_sqs.deleted_receipts == ["receipt-valid"]
        mock_meilisearch_client.index.return_value.add_documents.assert_called()

    def test_consumer_sns_wrapped_event_is_unwrapped_and_indexed(self, run_consumer, indexer, mock_meilisearch_client):
        """An SNS envelope is unwrapped before the inner event is indexed."""
        inner = json.dumps({"eventType": "file_uploaded", "fileId": "file-1", "name": "notes.txt"})
        body = json.dumps({"TopicArn": "arn:aws:sns:us-east-1:000000000000:events", "Message": inner})
        fake_sqs = run_consumer(indexer, [[_message("receipt-sns", body)]])

        assert fake_sqs.deleted_receipts == ["receipt-sns"]
        mock_meilisearch_client.index.return_value.add_documents.assert_called()

    def test_consumer_bad_message_does_not_stop_later_messages(self, run_consumer, indexer, mock_meilisearch_client):
        """A malformed message in a batch does not prevent the next one from indexing."""
        good_body = json.dumps(
            {"event_type": "document_created", "payload": {"id": "doc-2", "title": "Good"}}
        )
        fake_sqs = run_consumer(
            indexer,
            [[_message("receipt-bad", "{not-json", "msg-bad"), _message("receipt-good", good_body, "msg-good")]],
        )

        assert fake_sqs.deleted_receipts == ["receipt-bad", "receipt-good"]
        mock_meilisearch_client.index.return_value.add_documents.assert_called()


class TestSQSConsumerLifecycle:
    """Tests for starting and stopping the polling thread."""

    def test_consumer_stop_joins_polling_thread_within_timeout(self, run_consumer, indexer):
        """After stop() returns, no polling thread is left running."""
        run_consumer(indexer, [[]])

        assert _consumer_threads_alive() == []

    def test_consumer_without_queue_url_starts_no_thread(self, indexer):
        """With no queue configured the consumer stays idle instead of polling."""
        consumer = SQSConsumer(indexer=indexer, queue_url="")

        with patch("boto3.client") as mock_boto_client:
            consumer.start()
            try:
                assert _consumer_threads_alive() == []
                mock_boto_client.assert_not_called()
            finally:
                consumer.stop()

    def test_consumer_stop_before_start_is_a_no_op(self, indexer):
        """Stopping a consumer that was never started does not raise."""
        SQSConsumer(indexer=indexer, queue_url=QUEUE_URL).stop()

        assert _consumer_threads_alive() == []
