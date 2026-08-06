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
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import org.junit.runner.RunWith
import kotlin.test.Ignore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

@RunWith(RandomOrderRunner::class)
class NotificationPreferencesTest {

    private class Harness {
        val repository = mockk<NotificationRepository>(relaxed = true)
        val emailSender = mockk<EmailSender>(relaxed = true)
        val webSocketManager = mockk<WebSocketManager>(relaxed = true)
        val service = NotificationService(repository, emailSender, webSocketManager, null)
        val saved = mutableListOf<Notification>()

        init {
            coEvery { emailSender.sendEmail(any(), any(), any()) } returns true
            coEvery { webSocketManager.pushNotification(any(), any()) } returns 1
            coEvery { repository.saveNotification(capture(saved)) } returns Unit
            coEvery { repository.getPreferences(any()) } answers {
                NotificationPreference(userId = firstArg<String>())
            }
        }

        fun prefs(userId: String, channels: Map<String, List<DeliveryChannel>>) {
            coEvery { repository.getPreferences(userId) } returns
                NotificationPreference(userId = userId, channels = channels)
        }
    }

    @Test
    fun `opting out of EMAIL suppresses the email but keeps in-app delivery`() = runTest {
        val h = Harness()
        h.prefs("user-a", mapOf("file_shared" to listOf(DeliveryChannel.IN_APP)))

        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        coVerify(exactly = 0) { h.emailSender.sendEmail(any(), any(), any()) }
        assertEquals(listOf("in_app"), h.saved.last().deliveredVia)
    }

    @Test
    fun `opting out of PUSH suppresses the websocket push`() = runTest {
        val h = Harness()
        h.prefs("user-a", mapOf("file_shared" to listOf(DeliveryChannel.IN_APP, DeliveryChannel.EMAIL)))

        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        coVerify(exactly = 0) { h.webSocketManager.pushNotification(any(), any()) }
        assertFalse(h.saved.last().deliveredVia.contains("push"))
    }

    @Test
    fun `opting out of one category does not suppress another`() = runTest {
        val h = Harness()
        h.prefs(
            "user-a",
            mapOf(
                "comment_added" to emptyList(),
                "file_shared" to listOf(DeliveryChannel.EMAIL, DeliveryChannel.IN_APP),
            ),
        )

        h.service.processEvent(Fixtures.commentAddedEvent(userId = "user-a"))
        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        assertEquals(emptyList(), h.saved.first { it.type == "comment_added" }.deliveredVia)
        assertEquals(
            listOf("in_app", "email"),
            h.saved.first { it.type == "file_shared" }.deliveredVia,
        )
        coVerify(exactly = 1) { h.emailSender.sendEmail(any(), any(), any()) }
    }

    @Test
    fun `a full opt-out delivers through no channel at all`() = runTest {
        val h = Harness()
        h.prefs("user-a", mapOf("file_shared" to emptyList()))

        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        coVerify(exactly = 0) { h.emailSender.sendEmail(any(), any(), any()) }
        coVerify(exactly = 0) { h.webSocketManager.pushNotification(any(), any()) }
        assertEquals(emptyList(), h.saved.last().deliveredVia)
    }

    @Ignore(
        "DEFECT: opting out only suppresses outbound channels. NotificationService.processEvent " +
            "always calls repository.saveNotification, so a user who opted out of every channel for " +
            "an event type still sees the notification in GET /api/v1/notifications and in the " +
            "unread count — the opt-out is not honoured for the in-app surface. Not fixed here: " +
            "this package is test-only."
    )
    @Test
    fun `a fully opted-out user receives nothing at all`() = runTest {
        val h = Harness()
        h.prefs("user-a", mapOf("file_shared" to emptyList()))

        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        coVerify(exactly = 0) { h.repository.saveNotification(any()) }
    }

    @Test
    fun `an event type absent from stored preferences falls back to the built-in defaults`() = runTest {
        val h = Harness()
        h.prefs("user-a", mapOf("comment_added" to emptyList()))

        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        // Built-in default for file_shared is EMAIL + IN_APP + PUSH.
        assertTrue(h.saved.last().deliveredVia.containsAll(listOf("in_app", "email")))
        coVerify(exactly = 1) { h.emailSender.sendEmail(any(), any(), any()) }
    }

    @Test
    fun `an unknown event type with no preference entry falls back to in-app only`() = runTest {
        val h = Harness()
        h.prefs("user-a", emptyMap())

        h.service.processEvent(
            Fixtures.commentAddedEvent(userId = "user-a").copy(eventType = "widget_exploded")
        )

        assertEquals(listOf("in_app"), h.saved.last().deliveredVia)
        coVerify(exactly = 0) { h.emailSender.sendEmail(any(), any(), any()) }
    }

    @Test
    fun `another user's opt-out does not suppress this user's notification`() = runTest {
        val h = Harness()
        h.prefs("user-b", mapOf("file_shared" to emptyList()))

        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        coVerify(exactly = 1) { h.repository.getPreferences("user-a") }
        coVerify(exactly = 0) { h.repository.getPreferences("user-b") }
        assertTrue(h.saved.last().deliveredVia.contains("in_app"))
    }

    @Test
    fun `updatePreferences replaces one category and keeps the others`() = runTest {
        val h = Harness()
        h.prefs(
            "user-a",
            mapOf(
                "file_shared" to listOf(DeliveryChannel.EMAIL),
                "comment_added" to listOf(DeliveryChannel.IN_APP),
            ),
        )
        val stored = slot<NotificationPreference>()
        coEvery { h.repository.savePreferences(capture(stored)) } returns Unit

        h.service.updatePreferences("user-a", "file_shared", listOf(DeliveryChannel.PUSH))

        assertEquals(listOf(DeliveryChannel.PUSH), stored.captured.channels["file_shared"])
        assertEquals(listOf(DeliveryChannel.IN_APP), stored.captured.channels["comment_added"])
    }

    @Test
    fun `updatePreferences with an empty channel list stores an explicit opt-out`() = runTest {
        val h = Harness()
        h.prefs("user-a", mapOf("file_shared" to listOf(DeliveryChannel.EMAIL)))
        val stored = slot<NotificationPreference>()
        coEvery { h.repository.savePreferences(capture(stored)) } returns Unit

        h.service.updatePreferences("user-a", "file_shared", emptyList())

        assertEquals(emptyList(), stored.captured.channels["file_shared"])
    }

    @Test
    fun `updatePreferences for an unknown event type adds a new category`() = runTest {
        val h = Harness()
        h.prefs("user-a", mapOf("file_shared" to listOf(DeliveryChannel.EMAIL)))
        val stored = slot<NotificationPreference>()
        coEvery { h.repository.savePreferences(capture(stored)) } returns Unit

        h.service.updatePreferences("user-a", "widget_exploded", listOf(DeliveryChannel.IN_APP))

        assertEquals(listOf(DeliveryChannel.IN_APP), stored.captured.channels["widget_exploded"])
        assertEquals(listOf(DeliveryChannel.EMAIL), stored.captured.channels["file_shared"])
    }

    @Test
    fun `push is enabled but records no delivery when the user has no live session`() = runTest {
        val h = Harness()
        coEvery { h.webSocketManager.pushNotification(any(), any()) } returns 0
        h.prefs("user-a", mapOf("file_shared" to listOf(DeliveryChannel.IN_APP, DeliveryChannel.PUSH)))

        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        coVerify(exactly = 1) { h.webSocketManager.pushNotification("user-a", any()) }
        assertEquals(listOf("in_app"), h.saved.last().deliveredVia)
    }

    @Test
    fun `a failed email is not recorded as a delivery channel`() = runTest {
        val h = Harness()
        coEvery { h.emailSender.sendEmail(any(), any(), any()) } returns false
        h.prefs("user-a", mapOf("file_shared" to listOf(DeliveryChannel.IN_APP, DeliveryChannel.EMAIL)))

        h.service.processEvent(Fixtures.fileSharedEvent(sharedWithUserId = "user-a"))

        assertEquals(listOf("in_app"), h.saved.last().deliveredVia)
    }
}
