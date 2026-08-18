package com.otterworks.notification.template

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** The operator footer is resolved through the prefixed lookups, not hand-substituted. */
class NotificationFooterTest {

    @Test
    fun footerResolvesOperatorLookups() {
        assertEquals(
            "<!-- This is an automated message | ${NotificationTemplates.SIGNATURE} -->",
            NotificationTemplates.footer(),
        )
    }

    @Test
    fun eventVariablesAreStillSubstituted() {
        assertEquals(
            "hello u-1",
            NotificationTemplates.replaceVariables("hello {{actorId}}", mapOf("actorId" to "u-1")),
        )
    }

    @Test
    fun eventValuesContainingATokenAreInsertedLiterally() {
        assertEquals(
            "hello {{sys:user.name}}",
            NotificationTemplates.replaceVariables(
                "hello {{actorId}}",
                mapOf("actorId" to "{{sys:user.name}}"),
            ),
        )
    }

    @Test
    fun renderedEmailBodyCarriesTheFooter() {
        val rendered = NotificationTemplates.render(
            com.otterworks.notification.model.SqsNotificationMessage(
                eventType = "file_shared",
                ownerId = "owner-1",
                actorId = "actor-1",
                fileId = "file-1",
                timestamp = "2026-01-01T00:00:00Z",
            ),
        )
        assertTrue(rendered.emailBody.endsWith(NotificationTemplates.footer()))
    }
}
