package com.otterworks.notification.service

import com.otterworks.notification.model.DeliveryChannel
import com.otterworks.notification.model.Notification
import com.otterworks.notification.model.NotificationPreference
import com.otterworks.notification.model.SqsNotificationMessage
import com.otterworks.notification.repository.NotificationRepository
import com.otterworks.notification.websocket.WebSocketManager
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class NotificationServiceTest {

    private val repository = mockk<NotificationRepository>(relaxed = true)
    private val emailSender = mockk<EmailSender>(relaxed = true)
    private val webSocketManager = mockk<WebSocketManager>(relaxed = true)

    private val service = NotificationService(
        repository = repository,
        emailSender = emailSender,
        webSocketManager = webSocketManager,
        meterRegistry = null,
    )

    @Test
    fun `resolveTargetUserId returns sharedWithUserId for file_shared events`() {
        val event = SqsNotificationMessage(
            eventType = "file_shared",
            fileId = FILE_123,
            ownerId = OWNER_1,
            sharedWithUserId = USER_2,
            timestamp = TIMESTAMP,
        )
        assertEquals(USER_2, NotificationService.resolveTargetUserId(event))
    }

    @Test
    fun `resolveTargetUserId returns mentionedUserId for user_mentioned events`() {
        val event = SqsNotificationMessage(
            eventType = "user_mentioned",
            mentionedUserId = "mentioned-user",
            actorId = "actor-1",
            documentId = "doc-1",
            timestamp = TIMESTAMP,
        )
        assertEquals("mentioned-user", NotificationService.resolveTargetUserId(event))
    }

    @Test
    fun `resolveTargetUserId returns userId for comment_added events`() {
        val event = SqsNotificationMessage(
            eventType = "comment_added",
            userId = DOC_OWNER,
            actorId = "commenter",
            documentId = "doc-1",
            commentId = "comment-1",
            timestamp = TIMESTAMP,
        )
        assertEquals(DOC_OWNER, NotificationService.resolveTargetUserId(event))
    }

    @Test
    fun `resolveTargetUserId returns userId for document_edited events`() {
        val event = SqsNotificationMessage(
            eventType = "document_edited",
            userId = DOC_OWNER,
            actorId = "editor",
            documentId = "doc-1",
            timestamp = TIMESTAMP,
        )
        assertEquals(DOC_OWNER, NotificationService.resolveTargetUserId(event))
    }

    @Test
    fun `resolveResourceId returns fileId for file_shared`() {
        val event = SqsNotificationMessage(
            eventType = "file_shared",
            fileId = "file-abc",
            timestamp = TIMESTAMP,
        )
        assertEquals("file-abc", NotificationService.resolveResourceId(event))
    }

    @Test
    fun `resolveResourceId returns commentId for comment_added`() {
        val event = SqsNotificationMessage(
            eventType = "comment_added",
            commentId = "comment-xyz",
            documentId = "doc-1",
            timestamp = TIMESTAMP,
        )
        assertEquals("comment-xyz", NotificationService.resolveResourceId(event))
    }

    @Test
    fun `resolveResourceId returns documentId for document_edited`() {
        val event = SqsNotificationMessage(
            eventType = "document_edited",
            documentId = "doc-123",
            timestamp = TIMESTAMP,
        )
        assertEquals("doc-123", NotificationService.resolveResourceId(event))
    }

    @Test
    fun `resolveResourceType returns correct types`() {
        assertEquals("file", NotificationService.resolveResourceType(
            SqsNotificationMessage(eventType = "file_shared", timestamp = TIMESTAMP)
        ))
        assertEquals("comment", NotificationService.resolveResourceType(
            SqsNotificationMessage(eventType = "comment_added", timestamp = TIMESTAMP)
        ))
        assertEquals("document", NotificationService.resolveResourceType(
            SqsNotificationMessage(eventType = "document_edited", timestamp = TIMESTAMP)
        ))
        assertEquals("document", NotificationService.resolveResourceType(
            SqsNotificationMessage(eventType = "user_mentioned", timestamp = TIMESTAMP)
        ))
        assertEquals("unknown", NotificationService.resolveResourceType(
            SqsNotificationMessage(eventType = "other_event", timestamp = TIMESTAMP)
        ))
    }

    @Test
    fun `processEvent stores in-app notification and sends email for file_shared`() = runTest {
        val event = SqsNotificationMessage(
            eventType = "file_shared",
            fileId = FILE_123,
            ownerId = OWNER_1,
            sharedWithUserId = USER_2,
            timestamp = TIMESTAMP,
        )

        coEvery { repository.getPreferences(USER_2) } returns NotificationPreference(userId = USER_2)
        coEvery { emailSender.sendEmail(any(), any(), any()) } returns true

        service.processEvent(event)

        val savedNotifications = mutableListOf<Notification>()
        coVerify(atLeast = 1) { repository.saveNotification(capture(savedNotifications)) }

        val lastSaved = savedNotifications.last()
        assertEquals(USER_2, lastSaved.userId)
        assertEquals("file_shared", lastSaved.type)
        assertEquals("File Shared With You", lastSaved.title)
        assertTrue(lastSaved.deliveredVia.contains("in_app"))
    }

    @Test
    fun `processEvent skips email when not in preferences`() = runTest {
        val event = SqsNotificationMessage(
            eventType = "document_edited",
            userId = USER_1,
            actorId = "editor-1",
            documentId = "doc-1",
            timestamp = TIMESTAMP,
        )

        val prefs = NotificationPreference(
            userId = USER_1,
            channels = mapOf("document_edited" to listOf(DeliveryChannel.IN_APP)),
        )
        coEvery { repository.getPreferences(USER_1) } returns prefs

        service.processEvent(event)

        coVerify(exactly = 0) { emailSender.sendEmail(any(), any(), any()) }
        coVerify(atLeast = 1) { repository.saveNotification(any()) }
    }

    @Test
    fun `processEvent does nothing for blank target user`() = runTest {
        val event = SqsNotificationMessage(
            eventType = "file_shared",
            fileId = FILE_123,
            ownerId = OWNER_1,
            sharedWithUserId = "",
            timestamp = TIMESTAMP,
        )

        service.processEvent(event)

        coVerify(exactly = 0) { repository.saveNotification(any()) }
        coVerify(exactly = 0) { emailSender.sendEmail(any(), any(), any()) }
    }

    @Test
    fun `getNotifications delegates to repository`() = runTest {
        val notifications = listOf(
            Notification(
                id = "n-1",
                userId = USER_1,
                type = "file_shared",
                title = "Test",
                message = "Test msg",
                createdAt = TIMESTAMP,
            )
        )
        coEvery { repository.getNotificationsByUserId(USER_1, 1, 20) } returns Pair(notifications, 1)

        val (result, total) = service.getNotifications(USER_1, 1, 20)
        assertEquals(1, result.size)
        assertEquals(1, total)
        assertEquals("n-1", result[0].id)
    }

    @Test
    fun `markAsRead delegates to repository`() = runTest {
        coEvery { repository.markAsRead("n-1") } returns true
        assertTrue(service.markAsRead("n-1"))
    }

    @Test
    fun `markAllAsRead delegates to repository`() = runTest {
        coEvery { repository.markAllAsRead(USER_1) } returns 5
        assertEquals(5, service.markAllAsRead(USER_1))
    }

    @Test
    fun `getUnreadCount delegates to repository`() = runTest {
        coEvery { repository.getUnreadCount(USER_1) } returns 3
        assertEquals(3, service.getUnreadCount(USER_1))
    }

    @Test
    fun `processEvent sends push notification when PUSH channel enabled`() = runTest {
        val event = SqsNotificationMessage(
            eventType = "user_mentioned",
            mentionedUserId = USER_3,
            actorId = "actor-1",
            documentId = "doc-1",
            timestamp = TIMESTAMP,
        )

        coEvery { repository.getPreferences(USER_3) } returns NotificationPreference(userId = USER_3)
        coEvery { emailSender.sendEmail(any(), any(), any()) } returns true

        service.processEvent(event)

        coVerify { webSocketManager.pushNotification(USER_3, any()) }
    }

    companion object {
        private const val TIMESTAMP = "2024-01-01T00:00:00Z"
        private const val FILE_123 = "file-123"
        private const val OWNER_1 = "owner-1"
        private const val USER_2 = "user-2"
        private const val DOC_OWNER = "doc-owner"
        private const val USER_1 = "user-1"
        private const val USER_3 = "user-3"
    }
}
