package com.otterworks.notification.consumer

import aws.sdk.kotlin.services.sqs.SqsClient
import aws.sdk.kotlin.services.sqs.model.DeleteMessageRequest
import aws.sdk.kotlin.services.sqs.model.Message
import aws.sdk.kotlin.services.sqs.model.ReceiveMessageResponse
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.support.Fixtures
import com.otterworks.notification.support.RandomOrderRunner
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.runner.RunWith
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull

/**
 * Pins the poison-message path of the SQS consumer.
 *
 * There is no in-process dead-letter queue: `infrastructure/terraform/modules/messaging/main.tf`
 * puts a redrive policy with `maxReceiveCount = 3` on the notifications queue, so the DLQ is
 * reached by *not* deleting a message the consumer cannot handle. These tests therefore assert
 * the delete/no-delete decision, which is the behaviour the DLQ depends on.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RandomOrderRunner::class)
class SqsConsumerDlqTest {

    private fun message(body: String, id: String, receipt: String): Message =
        Message {
            this.body = body
            this.messageId = id
            this.receiptHandle = receipt
        }

    /**
     * Runs the polling loop over a single batch and returns once it is drained.
     *
     * The first poll yields [batch], the second yields nothing (the loop then suspends on its
     * virtual-time poll delay, which lets the per-message coroutines finish), and the third
     * cancels the loop. No wall-clock sleeping is involved: `runTest` drives the delay.
     */
    private fun runOneBatch(
        sqsClient: SqsClient,
        notificationService: NotificationService,
        batch: List<Message>,
        block: suspend () -> Unit,
    ) = runTest {
        var poll = 0
        lateinit var pollingJob: Job
        coEvery { sqsClient.receiveMessage(any()) } answers {
            when (poll++) {
                0 -> ReceiveMessageResponse { messages = batch }
                1 -> ReceiveMessageResponse { messages = emptyList() }
                else -> {
                    pollingJob.cancel()
                    ReceiveMessageResponse { messages = emptyList() }
                }
            }
        }

        val consumer = SqsConsumer(sqsClient, notificationService, Fixtures.config(), null)
        pollingJob = launch { consumer.startPolling() }
        advanceUntilIdle()
        block()
    }

    @Test
    fun `malformed message is left on the queue so SQS redrive can move it to the DLQ`() {
        val sqsClient = mockk<SqsClient>(relaxed = true)
        val notificationService = mockk<NotificationService>(relaxed = true)

        runOneBatch(
            sqsClient,
            notificationService,
            listOf(message("{ this is not json", "m-1", "receipt-1")),
        ) {
            coVerify(exactly = 0) { sqsClient.deleteMessage(any<DeleteMessageRequest>()) }
            coVerify(exactly = 0) { notificationService.processEvent(any()) }
        }
    }

    @Test
    fun `valid message is processed once and deleted from the queue`() {
        val sqsClient = mockk<SqsClient>(relaxed = true)
        val notificationService = mockk<NotificationService>(relaxed = true)

        runOneBatch(
            sqsClient,
            notificationService,
            listOf(message(Fixtures.fileSharedEventJson(), "m-1", "receipt-1")),
        ) {
            val deleted = slot<DeleteMessageRequest>()
            coVerify(exactly = 1) { sqsClient.deleteMessage(capture(deleted)) }
            assertEquals("receipt-1", deleted.captured.receiptHandle)
            coVerify(exactly = 1) { notificationService.processEvent(any()) }
        }
    }

    @Test
    fun `a batch with one poison message deletes only the message it could handle`() {
        val sqsClient = mockk<SqsClient>(relaxed = true)
        val notificationService = mockk<NotificationService>(relaxed = true)

        runOneBatch(
            sqsClient,
            notificationService,
            listOf(
                message(Fixtures.fileSharedEventJson(), "m-good", "receipt-good"),
                message("<xml>not json</xml>", "m-poison", "receipt-poison"),
            ),
        ) {
            val deleted = slot<DeleteMessageRequest>()
            coVerify(exactly = 1) { sqsClient.deleteMessage(capture(deleted)) }
            assertEquals("receipt-good", deleted.captured.receiptHandle)
            coVerify(exactly = 1) { notificationService.processEvent(any()) }
        }
    }

    @Test
    fun `an empty poll deletes nothing and processes nothing`() {
        val sqsClient = mockk<SqsClient>(relaxed = true)
        val notificationService = mockk<NotificationService>(relaxed = true)

        runOneBatch(sqsClient, notificationService, emptyList()) {
            coVerify(exactly = 0) { sqsClient.deleteMessage(any<DeleteMessageRequest>()) }
            coVerify(exactly = 0) { notificationService.processEvent(any()) }
        }
    }

    @Test
    fun `SNS envelope carrying a malformed payload is left on the queue`() {
        val sqsClient = mockk<SqsClient>(relaxed = true)
        val notificationService = mockk<NotificationService>(relaxed = true)
        val envelope =
            """{"Type":"Notification","MessageId":"sns-1","Message":"{\"nope\":true}"}"""

        runOneBatch(sqsClient, notificationService, listOf(message(envelope, "m-1", "receipt-1"))) {
            coVerify(exactly = 0) { sqsClient.deleteMessage(any<DeleteMessageRequest>()) }
            coVerify(exactly = 0) { notificationService.processEvent(any()) }
        }
    }

    // ── parseMessage: the predicate the delete/no-delete decision is built on ──

    private fun consumer(): SqsConsumer = SqsConsumer(
        mockk(relaxed = true),
        mockk(relaxed = true),
        Fixtures.config(),
        null,
    )

    @Test
    fun `parseMessage rejects a payload missing the required timestamp field`() {
        assertNull(consumer().parseMessage("""{"eventType":"file_shared","fileId":"f-1"}"""))
    }

    @Test
    fun `parseMessage rejects a payload missing the required eventType field`() {
        assertNull(consumer().parseMessage("""{"fileId":"f-1","timestamp":"${Fixtures.FIXED_TIMESTAMP}"}"""))
    }

    @Test
    fun `parseMessage rejects an empty body`() {
        assertNull(consumer().parseMessage(""))
    }

    @Test
    fun `parseMessage rejects a JSON array`() {
        assertNull(consumer().parseMessage("""[{"eventType":"file_shared"}]"""))
    }

    @Test
    fun `parseMessage ignores unknown fields on an otherwise valid payload`() {
        val event = consumer().parseMessage(
            """{"eventType":"file_shared","sharedWithUserId":"user-a",
               "somethingNew":"ignored","timestamp":"${Fixtures.FIXED_TIMESTAMP}"}"""
        )
        assertNotNull(event)
        assertEquals("user-a", event.sharedWithUserId)
    }
}
