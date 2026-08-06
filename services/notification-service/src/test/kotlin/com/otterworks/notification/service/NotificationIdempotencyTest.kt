package com.otterworks.notification.service

import com.otterworks.notification.model.DeliveryChannel
import com.otterworks.notification.model.Notification
import com.otterworks.notification.model.NotificationPreference
import com.otterworks.notification.repository.NotificationRepository
import com.otterworks.notification.support.Fixtures
import com.otterworks.notification.support.RandomOrderRunner
import com.otterworks.notification.websocket.WebSocketManager
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.runner.RunWith
import kotlin.test.Ignore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * SQS delivery is at-least-once, so the same notification event can reach the consumer twice.
 * These tests pin what the service does with a redelivery today.
 */
@RunWith(RandomOrderRunner::class)
class NotificationIdempotencyTest {

    private class Harness {
        val repository = mockk<NotificationRepository>(relaxed = true)
        val emailSender = mockk<EmailSender>(relaxed = true)
        val webSocketManager = mockk<WebSocketManager>(relaxed = true)
        val service = NotificationService(repository, emailSender, webSocketManager, null)
        val saved = mutableListOf<Notification>()

        init {
            coEvery { repository.getPreferences(any()) } answers {
                NotificationPreference(userId = firstArg<String>())
            }
            coEvery { emailSender.sendEmail(any(), any(), any()) } returns true
            coEvery { webSocketManager.pushNotification(any(), any()) } returns 0
            coEvery { repository.saveNotification(capture(saved)) } returns Unit
        }
    }

    @Test
    fun `redelivering the same event currently stores a second notification`() = runTest {
        val h = Harness()
        val event = Fixtures.fileSharedEvent()

        h.service.processEvent(event)
        h.service.processEvent(event)

        assertEquals(2, h.saved.size)
        assertEquals(2, h.saved.map { it.id }.distinct().size)
    }

    @Ignore(
        "DEFECT: notification delivery is not idempotent. NotificationService.processEvent " +
            "mints a fresh UUID per invocation and NotificationRepository.saveNotification is an " +
            "unconditional PutItem, so an SQS redelivery (at-least-once) of the same event stores a " +
            "second notification and the user sees a duplicate. A deterministic id derived from the " +
            "event (or a conditional put on an idempotency key) would flip this test green. " +
            "Not fixed here: this package is test-only."
    )
    @Test
    fun `redelivering the same event must produce exactly one notification`() = runTest {
        val h = Harness()
        val event = Fixtures.fileSharedEvent()

        h.service.processEvent(event)
        h.service.processEvent(event)

        assertEquals(1, h.saved.map { it.id }.distinct().size)
        assertEquals(1, h.saved.size)
    }

    @Test
    fun `two distinct events for the same user store two notifications`() = runTest {
        val h = Harness()

        h.service.processEvent(Fixtures.fileSharedEvent(fileId = "file-1"))
        h.service.processEvent(Fixtures.fileSharedEvent(fileId = "file-2"))

        assertEquals(2, h.saved.size)
        assertEquals(setOf("file-1", "file-2"), h.saved.map { it.resourceId }.toSet())
    }

    @Test
    fun `a successful websocket push re-saves the same notification id rather than a new row`() = runTest {
        val h = Harness()
        coEvery { h.webSocketManager.pushNotification(any(), any()) } returns 1

        h.service.processEvent(Fixtures.fileSharedEvent())

        assertEquals(2, h.saved.size)
        assertEquals(1, h.saved.map { it.id }.distinct().size)
        assertTrue(h.saved.last().deliveredVia.contains("push"))
    }

    @Test
    fun `redelivery does not re-send an email when the user opted out between deliveries`() = runTest {
        val h = Harness()
        val event = Fixtures.fileSharedEvent()

        h.service.processEvent(event)
        coEvery { h.repository.getPreferences("user-a") } returns NotificationPreference(
            userId = "user-a",
            channels = mapOf("file_shared" to listOf(DeliveryChannel.IN_APP)),
        )
        h.service.processEvent(event)

        coVerify(exactly = 1) { h.emailSender.sendEmail(any(), any(), any()) }
    }
}
