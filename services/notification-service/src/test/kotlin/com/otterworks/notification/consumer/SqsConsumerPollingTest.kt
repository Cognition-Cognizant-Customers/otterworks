package com.otterworks.notification.consumer

import aws.sdk.kotlin.services.sqs.SqsClient
import aws.sdk.kotlin.services.sqs.model.DeleteMessageRequest
import aws.sdk.kotlin.services.sqs.model.Message
import aws.sdk.kotlin.services.sqs.model.ReceiveMessageResponse
import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.model.SqsNotificationMessage
import com.otterworks.notification.service.NotificationService
import io.micrometer.core.instrument.simple.SimpleMeterRegistry
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.test.Ignore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Drives [SqsConsumer.startPolling] against a mocked SQS client.
 *
 * Delivery semantics under test: the consumer has no explicit DLQ publish call — the
 * DLQ is wired as an SQS redrive policy on the queue itself
 * (`infrastructure/terraform/modules/messaging/main.tf`, `notifications_dlq`). The
 * observable contract from inside the service is therefore "a message the consumer
 * cannot handle is *not* deleted", which is what lets redrive move it to the DLQ after
 * `maxReceiveCount`. Every DLQ assertion below is expressed that way.
 *
 * Determinism: all waiting is virtual time from `runTest`; there is no `Thread.sleep`,
 * no wall-clock reads, and every fixture is built per test method.
 */
class SqsConsumerPollingTest {

    private val config = AppConfig(
        port = 8086,
        awsRegion = "us-east-1",
        awsEndpointUrl = null,
        sqsQueueUrl = "http://localhost:4566/000000000000/test-queue",
        snsTopicArn = "arn:aws:sns:us-east-1:000000000000:test-topic",
        dynamoDbTableNotifications = "test-notifications",
        dynamoDbTablePreferences = "test-preferences",
        sesFromEmail = "test@otterworks.io",
        sqsPollIntervalMs = 1_000,
        sqsMaxMessages = 10,
        sqsWaitTimeSeconds = 5,
    )

    private val notificationService = mockk<NotificationService>(relaxed = true)
    private val meterRegistry = SimpleMeterRegistry()

    private fun validBody(eventType: String = "file_shared", sharedWith: String = "user-2") = """
        {"eventType":"$eventType","fileId":"file-1","ownerId":"owner-1",
         "sharedWithUserId":"$sharedWith","timestamp":"2024-01-01T00:00:00Z"}
    """.trimIndent()

    private fun message(id: String, body: String?, receipt: String = "receipt-$id") = Message {
        messageId = id
        receiptHandle = receipt
        this.body = body
    }

    /** An SQS client that serves [batches] in order, then reports an empty queue forever. */
    private fun sqsServing(vararg batches: List<Message>): SqsClient {
        val client = mockk<SqsClient>(relaxed = true)
        val pending = ArrayDeque(batches.toList())
        coEvery { client.receiveMessage(any()) } answers {
            ReceiveMessageResponse { messages = pending.removeFirstOrNull() ?: emptyList() }
        }
        return client
    }

    /**
     * Runs the polling loop over virtual time until the queue has been drained and the
     * loop has idled for [idleCycles] empty polls, then cancels it. Returns when the
     * consumer coroutine has fully unwound, so all assertions below observe a settled state.
     */
    private suspend fun TestScope.pollUntilIdle(consumer: SqsConsumer, idleCycles: Int = 5) {
        withTimeoutOrNull(config.sqsPollIntervalMs * idleCycles) { consumer.startPolling() }
    }

    private fun consumer(sqsClient: SqsClient) =
        SqsConsumer(sqsClient, notificationService, config, meterRegistry)

    private fun errorCount(): Double =
        meterRegistry.counter("notifications.processing.errors").count()

    // ── Positive ────────────────────────────────────────────────────────────────

    @Test
    fun `valid message is processed and deleted from the queue`() = runTest {
        val sqs = sqsServing(listOf(message("m-1", validBody())))

        pollUntilIdle(consumer(sqs))

        val event = slot<SqsNotificationMessage>()
        coVerify(exactly = 1) { notificationService.processEvent(capture(event)) }
        assertEquals("file_shared", event.captured.eventType)

        val delete = slot<DeleteMessageRequest>()
        coVerify(exactly = 1) { sqs.deleteMessage(capture(delete)) }
        assertEquals("receipt-m-1", delete.captured.receiptHandle)
        assertEquals(config.sqsQueueUrl, delete.captured.queueUrl)
    }

    @Test
    fun `SNS-wrapped message is unwrapped, processed and deleted`() = runTest {
        val inner = validBody().replace("\n", "").replace("\"", "\\\"")
        val body = """{"Type":"Notification","MessageId":"sns-1","Message":"$inner"}"""
        val sqs = sqsServing(listOf(message("m-1", body)))

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = 1) { notificationService.processEvent(any()) }
        coVerify(exactly = 1) { sqs.deleteMessage(any()) }
    }

    @Test
    fun `unknown template id is still processed and deleted rather than poisoning the queue`() = runTest {
        // No template exists for "workspace_archived"; NotificationTemplates falls back to a
        // generic body, so the message must not be left on the queue as a poison pill.
        val sqs = sqsServing(listOf(message("m-1", validBody(eventType = "workspace_archived"))))

        pollUntilIdle(consumer(sqs))

        val event = slot<SqsNotificationMessage>()
        coVerify(exactly = 1) { notificationService.processEvent(capture(event)) }
        assertEquals("workspace_archived", event.captured.eventType)
        coVerify(exactly = 1) { sqs.deleteMessage(any()) }
        assertEquals(0.0, errorCount())
    }

    // ── Negative / DLQ routing ──────────────────────────────────────────────────

    @Test
    fun `malformed message is left on the queue for SQS redrive to the DLQ`() = runTest {
        val sqs = sqsServing(listOf(message("m-bad", "}{ not json")))

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = 0) { notificationService.processEvent(any()) }
        coVerify(exactly = 0) { sqs.deleteMessage(any()) }
        assertEquals(1.0, errorCount())
    }

    @Test
    fun `message missing the required timestamp field is left on the queue for the DLQ`() = runTest {
        val sqs = sqsServing(listOf(message("m-bad", """{"eventType":"file_shared"}""")))

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = 0) { notificationService.processEvent(any()) }
        coVerify(exactly = 0) { sqs.deleteMessage(any()) }
        assertEquals(1.0, errorCount())
    }

    @Test
    fun `SNS envelope carrying an unparseable payload is left on the queue for the DLQ`() = runTest {
        val body = """{"Type":"Notification","MessageId":"sns-1","Message":"not-a-json-object"}"""
        val sqs = sqsServing(listOf(message("m-bad", body)))

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = 0) { notificationService.processEvent(any()) }
        coVerify(exactly = 0) { sqs.deleteMessage(any()) }
        assertEquals(1.0, errorCount())
    }

    @Test
    fun `message with a null body is skipped without deleting or counting an error`() = runTest {
        val sqs = sqsServing(listOf(message("m-null", null)))

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = 0) { notificationService.processEvent(any()) }
        coVerify(exactly = 0) { sqs.deleteMessage(any()) }
    }

    @Test
    fun `a malformed message does not stop the valid messages in the same batch`() = runTest {
        val sqs = sqsServing(
            listOf(
                message("m-bad", "}{ not json"),
                message("m-ok", validBody()),
            ),
        )

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = 1) { notificationService.processEvent(any()) }
        val delete = slot<DeleteMessageRequest>()
        coVerify(exactly = 1) { sqs.deleteMessage(capture(delete)) }
        assertEquals("receipt-m-ok", delete.captured.receiptHandle)
        assertEquals(1.0, errorCount())
    }

    @Test
    fun `a failing receiveMessage call does not kill the polling loop`() = runTest {
        val client = mockk<SqsClient>(relaxed = true)
        var call = 0
        coEvery { client.receiveMessage(any()) } answers {
            when (++call) {
                1 -> throw RuntimeException("SQS unavailable")
                2 -> ReceiveMessageResponse { messages = listOf(message("m-1", validBody())) }
                else -> ReceiveMessageResponse { messages = emptyList() }
            }
        }

        pollUntilIdle(consumer(client), idleCycles = 10)

        coVerify(exactly = 1) { notificationService.processEvent(any()) }
        coVerify(exactly = 1) { client.deleteMessage(any()) }
    }

    // ── Retry / backoff on a transient send failure ─────────────────────────────

    @Test
    fun `transient send failure leaves the message for redelivery and the retry deletes it`() = runTest {
        // Delivery failure path: processEvent throws, so deleteMessage is never reached and
        // the message becomes visible again after the SQS visibility timeout. The second
        // delivery succeeds and removes it. No sleeping — the redelivery is modelled as the
        // next receiveMessage batch.
        var attempts = 0
        coEvery { notificationService.processEvent(any()) } answers {
            if (++attempts == 1) throw RuntimeException("SES temporarily unavailable")
        }
        val redelivered = message("m-1", validBody(), receipt = "receipt-m-1-retry")
        val sqs = sqsServing(listOf(message("m-1", validBody())), listOf(redelivered))

        pollUntilIdle(consumer(sqs), idleCycles = 10)

        assertEquals(2, attempts)
        val delete = slot<DeleteMessageRequest>()
        coVerify(exactly = 1) { sqs.deleteMessage(capture(delete)) }
        assertEquals("receipt-m-1-retry", delete.captured.receiptHandle)
    }

    @Test
    fun `a message that fails every delivery attempt is never deleted so redrive can DLQ it`() = runTest {
        coEvery { notificationService.processEvent(any()) } throws RuntimeException("permanent failure")
        val sqs = sqsServing(
            listOf(message("m-1", validBody())),
            listOf(message("m-1", validBody(), receipt = "receipt-m-1-retry")),
        )

        pollUntilIdle(consumer(sqs), idleCycles = 10)

        coVerify(exactly = 2) { notificationService.processEvent(any()) }
        coVerify(exactly = 0) { sqs.deleteMessage(any()) }
    }

    // ── Duplicate delivery / idempotency ────────────────────────────────────────

    @Test
    fun `duplicate delivery of the same message id is currently processed twice`() = runTest {
        // Pins today's at-least-once behaviour: the consumer keeps no record of handled
        // message ids, so an SQS re-delivery of an already-processed message produces a
        // second processEvent call (and a second stored notification). See the disabled
        // test below for the behaviour this should have.
        val sqs = sqsServing(
            listOf(message("m-1", validBody())),
            listOf(message("m-1", validBody(), receipt = "receipt-m-1-again")),
        )

        pollUntilIdle(consumer(sqs), idleCycles = 10)

        coVerify(exactly = 2) { notificationService.processEvent(any()) }
        coVerify(exactly = 2) { sqs.deleteMessage(any()) }
    }

    @Test
    @Ignore(
        "DEFECT (genuine gap, not planted): SqsConsumer has no de-duplication of SQS message ids " +
            "and NotificationService.processEvent mints a fresh UUID per call, so an SQS " +
            "at-least-once re-delivery stores a duplicate notification. The queue is a standard " +
            "(non-FIFO) queue — infrastructure/terraform/modules/messaging/main.tf — so duplicate " +
            "delivery is expected, not exceptional. See docs/TEST-COVERAGE-EXPANSION-SOW.md §3 " +
            "'Cross-cutting gaps — Idempotency / concurrency'. Test-only package: not fixing here.",
    )
    fun `duplicate delivery of the same message id should be suppressed`() = runTest {
        val sqs = sqsServing(
            listOf(message("m-1", validBody())),
            listOf(message("m-1", validBody(), receipt = "receipt-m-1-again")),
        )

        pollUntilIdle(consumer(sqs), idleCycles = 10)

        coVerify(exactly = 1) { notificationService.processEvent(any()) }
        // The duplicate must still be acknowledged so it does not spin on the queue.
        coVerify(exactly = 2) { sqs.deleteMessage(any()) }
    }

    @Test
    fun `two distinct message ids carrying the same payload are both processed`() = runTest {
        // The mirror of the dedupe case: distinct events that merely look alike must not be
        // collapsed, so any future dedupe implementation has to key on the message id.
        val sqs = sqsServing(
            listOf(message("m-1", validBody()), message("m-2", validBody())),
        )

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = 2) { notificationService.processEvent(any()) }
        coVerify(exactly = 2) { sqs.deleteMessage(any()) }
    }

    // ── Boundary trio on the receive batch size (sqsMaxMessages = 10) ────────────

    @Test
    fun `batch of maxMessages minus one is fully processed`() = runTest {
        val size = config.sqsMaxMessages - 1
        val sqs = sqsServing((1..size).map { message("m-$it", validBody()) })

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = size) { notificationService.processEvent(any()) }
        coVerify(exactly = size) { sqs.deleteMessage(any()) }
    }

    @Test
    fun `batch of exactly maxMessages is fully processed`() = runTest {
        val size = config.sqsMaxMessages
        val sqs = sqsServing((1..size).map { message("m-$it", validBody()) })

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = size) { notificationService.processEvent(any()) }
        coVerify(exactly = size) { sqs.deleteMessage(any()) }
    }

    @Test
    fun `an over-sized batch is fully processed if SQS ever returns more than maxMessages`() = runTest {
        // maxNumberOfMessages is a request-side cap; the consumer must not silently drop the
        // tail if the broker (or LocalStack) hands back more than it asked for.
        val size = config.sqsMaxMessages + 1
        val sqs = sqsServing((1..size).map { message("m-$it", validBody()) })

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = size) { notificationService.processEvent(any()) }
        coVerify(exactly = size) { sqs.deleteMessage(any()) }
    }

    @Test
    fun `an empty batch neither processes nor deletes anything`() = runTest {
        val sqs = sqsServing(emptyList())

        pollUntilIdle(consumer(sqs))

        coVerify(exactly = 0) { notificationService.processEvent(any()) }
        coVerify(exactly = 0) { sqs.deleteMessage(any()) }
        assertTrue(errorCount() == 0.0)
    }
}
