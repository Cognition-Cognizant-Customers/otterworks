package com.otterworks.notification.service

import aws.sdk.kotlin.services.ses.SesClient
import aws.sdk.kotlin.services.ses.model.SendEmailRequest
import aws.sdk.kotlin.services.ses.model.SendEmailResponse
import com.otterworks.notification.config.AppConfig
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** SES delivery: the request that gets built, and the swallow-and-report-false failure path. */
class EmailSenderTest {

    private val config = AppConfig(
        port = 8086,
        awsRegion = "us-east-1",
        awsEndpointUrl = null,
        sqsQueueUrl = "http://localhost:4566/000000000000/test-queue",
        snsTopicArn = "arn:aws:sns:us-east-1:000000000000:test-topic",
        dynamoDbTableNotifications = "test-notifications",
        dynamoDbTablePreferences = "test-preferences",
        sesFromEmail = "notifications@otterworks.io",
        sqsPollIntervalMs = 1_000,
        sqsMaxMessages = 10,
        sqsWaitTimeSeconds = 5,
    )

    private val ses = mockk<SesClient>(relaxed = true)
    private val sender = EmailSender(ses, config)

    @Test
    fun `a delivered email reports success and is addressed from the configured sender`() = runTest {
        coEvery { ses.sendEmail(any()) } returns SendEmailResponse { messageId = "ses-1" }

        assertTrue(sender.sendEmail("user-2@otterworks.io", "Subject line", "<p>Body</p>"))

        val request = slot<SendEmailRequest>()
        coVerify(exactly = 1) { ses.sendEmail(capture(request)) }
        assertEquals("notifications@otterworks.io", request.captured.source)
        assertEquals(listOf("user-2@otterworks.io"), request.captured.destination?.toAddresses)
        assertEquals("Subject line", request.captured.message?.subject?.data)
        assertEquals("<p>Body</p>", request.captured.message?.body?.html?.data)
        assertEquals("UTF-8", request.captured.message?.body?.html?.charset)
    }

    @Test
    fun `an SES failure reports false instead of propagating`() = runTest {
        coEvery { ses.sendEmail(any()) } throws RuntimeException("MessageRejected")

        assertFalse(sender.sendEmail("user-2@otterworks.io", "Subject line", "<p>Body</p>"))
    }

    @Test
    fun `an empty subject and body are still sent rather than rejected locally`() = runTest {
        // EmailSender does no validation of its own; SES owns that decision.
        coEvery { ses.sendEmail(any()) } returns SendEmailResponse { messageId = "ses-2" }

        assertTrue(sender.sendEmail("user-2@otterworks.io", "", ""))

        coVerify(exactly = 1) { ses.sendEmail(any()) }
    }
}
