package com.otterworks.notification.service

import com.otterworks.notification.model.DeliveryChannel
import com.otterworks.notification.model.Notification
import com.otterworks.notification.model.NotificationPreference
import com.otterworks.notification.model.SqsNotificationMessage
import com.otterworks.notification.repository.NotificationRepository
import com.otterworks.notification.websocket.WebSocketManager
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Per-user delivery-preference matrix for [NotificationService.processEvent]:
 * every channel is exercised opted-in and opted-out, plus the failure paths of each
 * transport. Fixtures are per-method, so no test can observe another's mocks.
 */
class NotificationPreferencesTest {

    private val repository = mockk<NotificationRepository>(relaxed = true)
    private val emailSender = mockk<EmailSender>(relaxed = true)
    private val webSocketManager = mockk<WebSocketManager>(relaxed = true)

    private val service = NotificationService(
        repository = repository,
        emailSender = emailSender,
        webSocketManager = webSocketManager,
        meterRegistry = null,
    )

    private val fileShared = SqsNotificationMessage(
        eventType = "file_shared",
        fileId = "file-1",
        ownerId = "owner-1",
        actorId = "actor-1",
        sharedWithUserId = "user-2",
        timestamp = "2024-01-01T00:00:00Z",
    )

    private fun preferences(vararg channels: DeliveryChannel) = NotificationPreference(
        userId = "user-2",
        channels = mapOf("file_shared" to channels.toList()),
    )

    private fun savedNotifications(): List<Notification> {
        val saved = mutableListOf<Notification>()
        coVerify { repository.saveNotification(capture(saved)) }
        return saved
    }

    // ── Opt-in (positive) ───────────────────────────────────────────────────────

    @Test
    fun `email opt-in delivers the email and records the channel`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns
            preferences(DeliveryChannel.EMAIL, DeliveryChannel.IN_APP)
        coEvery { emailSender.sendEmail(any(), any(), any()) } returns true

        service.processEvent(fileShared)

        val subject = slot<String>()
        coVerify(exactly = 1) {
            emailSender.sendEmail("user-2@otterworks.io", capture(subject), any())
        }
        assertEquals("OtterWorks: A file has been shared with you", subject.captured)
        assertTrue(savedNotifications().last().deliveredVia.contains("email"))
    }

    @Test
    fun `push opt-in with a live session records the push channel on a second save`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns
            preferences(DeliveryChannel.IN_APP, DeliveryChannel.PUSH)
        coEvery { webSocketManager.pushNotification(eq("user-2"), any()) } returns 1
        // The channel list is snapshotted at call time: processEvent hands the same mutable
        // list to both writes, so a captured reference would show the post-push state twice.
        val writes = mutableListOf<Pair<String, List<String>>>()
        coEvery { repository.saveNotification(any()) } answers {
            val saved = firstArg<Notification>()
            writes.add(saved.id to saved.deliveredVia.toList())
        }

        service.processEvent(fileShared)

        coVerify(exactly = 1) { webSocketManager.pushNotification("user-2", any()) }
        assertEquals(2, writes.size)
        assertEquals(listOf("in_app"), writes.first().second)
        assertEquals(listOf("in_app", "push"), writes.last().second)
        assertEquals(writes.first().first, writes.last().first)
    }

    @Test
    fun `an event type absent from the stored preferences falls back to the service defaults`() = runTest {
        // Preferences exist for the user but say nothing about file_shared, so the default
        // map (EMAIL + IN_APP + PUSH) applies rather than a bare IN_APP.
        coEvery { repository.getPreferences("user-2") } returns NotificationPreference(
            userId = "user-2",
            channels = mapOf("document_edited" to listOf(DeliveryChannel.IN_APP)),
        )
        coEvery { emailSender.sendEmail(any(), any(), any()) } returns true

        service.processEvent(fileShared)

        coVerify(exactly = 1) { emailSender.sendEmail(any(), any(), any()) }
        coVerify(exactly = 1) { webSocketManager.pushNotification("user-2", any()) }
    }

    // ── Opt-out (negative) ──────────────────────────────────────────────────────

    @Test
    fun `email opt-out suppresses the email but still stores the in-app notification`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns preferences(DeliveryChannel.IN_APP)

        service.processEvent(fileShared)

        coVerify(exactly = 0) { emailSender.sendEmail(any(), any(), any()) }
        val saved = savedNotifications().last()
        assertEquals(listOf("in_app"), saved.deliveredVia)
        assertEquals("user-2", saved.userId)
    }

    @Test
    fun `push opt-out never touches the websocket manager`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns
            preferences(DeliveryChannel.EMAIL, DeliveryChannel.IN_APP)
        coEvery { emailSender.sendEmail(any(), any(), any()) } returns true

        service.processEvent(fileShared)

        coVerify(exactly = 0) { webSocketManager.pushNotification(any(), any()) }
        assertEquals(1, savedNotifications().size)
    }

    @Test
    fun `in-app opt-out drops the in_app channel from the stored record`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns preferences(DeliveryChannel.EMAIL)
        coEvery { emailSender.sendEmail(any(), any(), any()) } returns true

        service.processEvent(fileShared)

        val saved = savedNotifications().last()
        assertFalse(saved.deliveredVia.contains("in_app"))
        assertEquals(listOf("email"), saved.deliveredVia)
    }

    @Test
    fun `opting out of every channel still persists the notification for the audit trail`() = runTest {
        // Pinned deliberately: an empty channel list is "deliver nowhere", not "drop the
        // record" — the row is what /api/v1/notifications later reads back.
        coEvery { repository.getPreferences("user-2") } returns preferences()

        service.processEvent(fileShared)

        coVerify(exactly = 0) { emailSender.sendEmail(any(), any(), any()) }
        coVerify(exactly = 0) { webSocketManager.pushNotification(any(), any()) }
        val saved = savedNotifications().last()
        assertEquals(emptyList(), saved.deliveredVia)
    }

    // ── Transport failures ──────────────────────────────────────────────────────

    @Test
    fun `a failed email is not recorded as delivered`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns
            preferences(DeliveryChannel.EMAIL, DeliveryChannel.IN_APP)
        coEvery { emailSender.sendEmail(any(), any(), any()) } returns false

        service.processEvent(fileShared)

        val saved = savedNotifications().last()
        assertEquals(listOf("in_app"), saved.deliveredVia)
    }

    @Test
    fun `a push with no connected session is not recorded as delivered`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns
            preferences(DeliveryChannel.IN_APP, DeliveryChannel.PUSH)
        coEvery { webSocketManager.pushNotification(eq("user-2"), any()) } returns 0

        service.processEvent(fileShared)

        val saved = savedNotifications()
        assertEquals(1, saved.size)
        assertEquals(listOf("in_app"), saved.single().deliveredVia)
    }

    // ── Unknown template id ─────────────────────────────────────────────────────

    @Test
    fun `an unknown event type renders the fallback template and is stored`() = runTest {
        val unknown = SqsNotificationMessage(
            eventType = "workspace_archived",
            userId = "user-9",
            actorId = "actor-1",
            timestamp = "2024-01-01T00:00:00Z",
        )
        coEvery { repository.getPreferences("user-9") } returns NotificationPreference(userId = "user-9")

        service.processEvent(unknown)

        val saved = savedNotifications().last()
        assertEquals("Notification", saved.title)
        assertEquals("You have a new notification.", saved.message)
        assertEquals("workspace_archived", saved.type)
        assertEquals("unknown", saved.resourceType)
        assertEquals("", saved.resourceId)
        // An unknown type has no default channel entry, so it degrades to in-app only.
        assertEquals(listOf("in_app"), saved.deliveredVia)
        coVerify(exactly = 0) { emailSender.sendEmail(any(), any(), any()) }
    }

    @Test
    fun `an unknown event type with no target user is dropped`() = runTest {
        val unknown = SqsNotificationMessage(
            eventType = "workspace_archived",
            userId = "",
            timestamp = "2024-01-01T00:00:00Z",
        )

        service.processEvent(unknown)

        coVerify(exactly = 0) { repository.getPreferences(any()) }
        coVerify(exactly = 0) { repository.saveNotification(any()) }
    }

    @Test
    fun `updatePreferences merges the new channel list into the existing map`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns NotificationPreference(userId = "user-2")

        service.updatePreferences("user-2", "file_shared", listOf(DeliveryChannel.IN_APP))

        val saved = slot<NotificationPreference>()
        coVerify(exactly = 1) { repository.savePreferences(capture(saved)) }
        assertEquals(listOf(DeliveryChannel.IN_APP), saved.captured.channels["file_shared"])
        // The untouched event types survive the merge.
        assertEquals(
            listOf(DeliveryChannel.IN_APP),
            saved.captured.channels["document_edited"],
        )
    }

    @Test
    fun `updatePreferences can register an event type that has no default`() = runTest {
        coEvery { repository.getPreferences("user-2") } returns NotificationPreference(userId = "user-2")

        service.updatePreferences("user-2", "workspace_archived", listOf(DeliveryChannel.EMAIL))

        val saved = slot<NotificationPreference>()
        coVerify(exactly = 1) { repository.savePreferences(capture(saved)) }
        assertEquals(listOf(DeliveryChannel.EMAIL), saved.captured.channels["workspace_archived"])
        assertEquals(5, saved.captured.channels.size)
    }
}
