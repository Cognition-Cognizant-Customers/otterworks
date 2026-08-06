package com.otterworks.notification.template

import com.otterworks.notification.model.Notification
import com.otterworks.notification.model.NotificationPreference
import com.otterworks.notification.repository.NotificationRepository
import com.otterworks.notification.service.EmailSender
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.support.Fixtures
import com.otterworks.notification.support.RandomOrderRunner
import com.otterworks.notification.websocket.WebSocketManager
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.runner.RunWith
import kotlin.test.Ignore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

@RunWith(RandomOrderRunner::class)
class NotificationTemplateEdgeCasesTest {

    @Test
    fun `an unknown template id renders the generic fallback instead of failing`() {
        val rendered = NotificationTemplates.render(
            Fixtures.fileSharedEvent().copy(eventType = "no_such_template")
        )

        assertEquals("Notification", rendered.title)
        assertEquals("You have a new notification.", rendered.message)
        assertEquals("OtterWorks: New notification", rendered.emailSubject)
        assertFalse(rendered.emailBody.contains("{{"))
    }

    @Test
    fun `an empty template id renders the generic fallback`() {
        val rendered = NotificationTemplates.render(Fixtures.fileSharedEvent().copy(eventType = ""))
        assertEquals("Notification", rendered.title)
    }

    @Test
    fun `an unknown template id still stores a notification of that type`() = runTest {
        val repository = mockk<NotificationRepository>(relaxed = true)
        val saved = mutableListOf<Notification>()
        coEvery { repository.saveNotification(capture(saved)) } returns Unit
        coEvery { repository.getPreferences(any()) } answers {
            NotificationPreference(userId = firstArg<String>())
        }
        val service = NotificationService(
            repository,
            mockk<EmailSender>(relaxed = true),
            mockk<WebSocketManager>(relaxed = true),
            null,
        )

        service.processEvent(
            Fixtures.commentAddedEvent(userId = "user-a").copy(eventType = "no_such_template")
        )

        assertEquals("no_such_template", saved.last().type)
        assertEquals("Notification", saved.last().title)
        assertEquals("unknown", saved.last().resourceType)
        assertEquals("", saved.last().resourceId)
    }

    @Test
    fun `missing substitution variables render as empty strings and leave no placeholders`() {
        val rendered = NotificationTemplates.render(
            Fixtures.fileSharedEvent(fileId = "", ownerId = "").copy(actorId = "")
        )

        assertFalse(rendered.title.contains("{{"))
        assertFalse(rendered.message.contains("{{"))
        assertFalse(rendered.emailSubject.contains("{{"))
        assertFalse(rendered.emailBody.contains("{{"))
        assertEquals("A file has been shared with you by user .", rendered.message)
    }

    @Test
    fun `every known template id renders without leaving placeholders`() {
        val events = listOf(
            Fixtures.fileSharedEvent(),
            Fixtures.commentAddedEvent(),
            Fixtures.commentAddedEvent().copy(eventType = "document_edited"),
            Fixtures.commentAddedEvent().copy(eventType = "user_mentioned"),
        )

        events.forEach { event ->
            val rendered = NotificationTemplates.render(event)
            assertFalse(rendered.title.contains("{{"), "title for ${event.eventType}")
            assertFalse(rendered.message.contains("{{"), "message for ${event.eventType}")
            assertFalse(rendered.emailBody.contains("{{"), "body for ${event.eventType}")
            assertTrue(rendered.emailSubject.startsWith("OtterWorks: "))
        }
    }

    @Test
    fun `a value that looks like a placeholder is currently substituted a second time`() {
        val rendered = NotificationTemplates.render(
            Fixtures.fileSharedEvent(fileId = "file-1").copy(actorId = "{{fileId}}")
        )

        // Documents the current behaviour: the injected "{{fileId}}" is resolved by the
        // substitution pass that follows actorId, so the actor renders as the file id.
        assertEquals("A file has been shared with you by user file-1.", rendered.message)
    }

    @Ignore(
        "DEFECT: NotificationTemplates.replaceVariables applies substitutions sequentially over a " +
            "shared string, so a value inserted by an earlier variable is re-scanned by later ones. " +
            "Event data that contains '{{...}}' is therefore interpreted as template syntax " +
            "(template injection). Substituting in a single pass would flip this test green. " +
            "Not fixed here: this package is test-only."
    )
    @Test
    fun `a value that looks like a placeholder is inserted verbatim`() {
        val rendered = NotificationTemplates.render(
            Fixtures.fileSharedEvent(fileId = "file-1").copy(actorId = "{{fileId}}")
        )

        assertEquals("A file has been shared with you by user {{fileId}}.", rendered.message)
    }
}
