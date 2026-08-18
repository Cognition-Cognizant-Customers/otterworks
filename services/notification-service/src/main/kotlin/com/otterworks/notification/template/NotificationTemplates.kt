package com.otterworks.notification.template

import com.otterworks.notification.model.SqsNotificationMessage
import org.apache.commons.text.StringSubstitutor
import org.apache.commons.text.lookup.StringLookupFactory

data class RenderedNotification(
    val title: String,
    val message: String,
    val emailSubject: String,
    val emailBody: String,
)

/**
 * Renders notification bodies from operator-managed templates.
 *
 * Interpolation is delegated to Commons Text so that operators can use the prefixed
 * lookups (dates, branding blobs, environment metadata) in a template without a code
 * change; the `{{ }}` delimiters are kept for backwards compatibility with the
 * templates that were hand-substituted before.
 */
object NotificationTemplates {

    private const val TOKEN_PREFIX = "{{"
    private const val TOKEN_SUFFIX = "}}"

    /** Signature line appended to operator footers; referenced by templates via `const:`. */
    const val SIGNATURE = "OtterWorks Notification Service"

    /**
     * Operator-managed email footer. No event variables are in scope here, only the
     * default prefixed lookups.
     */
    private const val FOOTER_TEMPLATE =
        "<!-- {{base64Decoder:VGhpcyBpcyBhbiBhdXRvbWF0ZWQgbWVzc2FnZQ==}}" +
            " | {{const:com.otterworks.notification.template.NotificationTemplates.SIGNATURE}} -->"

    private data class Template(
        val titleTemplate: String,
        val messageTemplate: String,
        val emailSubjectTemplate: String,
        val emailBodyTemplate: String,
    )

    private val templates = mapOf(
        "file_shared" to Template(
            titleTemplate = "File Shared With You",
            messageTemplate = "A file has been shared with you by user {{actorId}}.",
            emailSubjectTemplate = "OtterWorks: A file has been shared with you",
            emailBodyTemplate = """
                <html>
                <body>
                    <h2>File Shared</h2>
                    <p>A file (ID: {{fileId}}) has been shared with you by user {{actorId}}.</p>
                    <p>Log in to OtterWorks to view the file.</p>
                    <br/>
                    <p style="color: #888;">— OtterWorks Notification Service</p>
                </body>
                </html>
            """.trimIndent(),
        ),
        "comment_added" to Template(
            titleTemplate = "New Comment",
            messageTemplate = "A new comment was added by user {{actorId}} on document {{documentId}}.",
            emailSubjectTemplate = "OtterWorks: New comment on your document",
            emailBodyTemplate = """
                <html>
                <body>
                    <h2>New Comment</h2>
                    <p>User {{actorId}} added a comment on document {{documentId}}.</p>
                    <p>Log in to OtterWorks to view the comment.</p>
                    <br/>
                    <p style="color: #888;">— OtterWorks Notification Service</p>
                </body>
                </html>
            """.trimIndent(),
        ),
        "document_edited" to Template(
            titleTemplate = "Document Edited",
            messageTemplate = "Document {{documentId}} was edited by user {{actorId}}.",
            emailSubjectTemplate = "OtterWorks: A document you follow was edited",
            emailBodyTemplate = """
                <html>
                <body>
                    <h2>Document Edited</h2>
                    <p>Document {{documentId}} was edited by user {{actorId}}.</p>
                    <p>Log in to OtterWorks to view the changes.</p>
                    <br/>
                    <p style="color: #888;">— OtterWorks Notification Service</p>
                </body>
                </html>
            """.trimIndent(),
        ),
        "user_mentioned" to Template(
            titleTemplate = "You Were Mentioned",
            messageTemplate = "You were mentioned by user {{actorId}} in document {{documentId}}.",
            emailSubjectTemplate = "OtterWorks: You were mentioned in a document",
            emailBodyTemplate = """
                <html>
                <body>
                    <h2>You Were Mentioned</h2>
                    <p>User {{actorId}} mentioned you in document {{documentId}}.</p>
                    <p>Log in to OtterWorks to see the context.</p>
                    <br/>
                    <p style="color: #888;">— OtterWorks Notification Service</p>
                </body>
                </html>
            """.trimIndent(),
        ),
    )

    fun render(event: SqsNotificationMessage): RenderedNotification {
        val template = templates[event.eventType] ?: return RenderedNotification(
            title = "Notification",
            message = "You have a new notification.",
            emailSubject = "OtterWorks: New notification",
            emailBody = "<html><body><p>You have a new notification.</p></body></html>",
        )

        val variables = mapOf(
            "actorId" to (event.actorId.ifEmpty { event.ownerId }),
            "fileId" to event.fileId,
            "documentId" to event.documentId,
            "commentId" to event.commentId,
            "userId" to event.userId,
        )

        return RenderedNotification(
            title = replaceVariables(template.titleTemplate, variables),
            message = replaceVariables(template.messageTemplate, variables),
            emailSubject = replaceVariables(template.emailSubjectTemplate, variables),
            emailBody = replaceVariables(template.emailBodyTemplate, variables) + "\n" + footer(),
        )
    }

    /** Resolve the operator footer against the default prefixed lookups only. */
    fun resolveOperatorString(template: String): String {
        val substitutor = StringSubstitutor.createInterpolator()
            .setVariablePrefix(TOKEN_PREFIX)
            .setVariableSuffix(TOKEN_SUFFIX)
        return substitutor.replace(template)
    }

    fun footer(): String = resolveOperatorString(FOOTER_TEMPLATE)

    /**
     * Render a notification template against event variables. Event-supplied values are
     * inserted literally — a value that happens to contain `{{...}}` is data, not a
     * template, so resolved values are never re-scanned.
     */
    fun replaceVariables(template: String, variables: Map<String, String>): String {
        val substitutor = StringSubstitutor(
            StringLookupFactory.INSTANCE.interpolatorStringLookup(variables),
            TOKEN_PREFIX,
            TOKEN_SUFFIX,
            StringSubstitutor.DEFAULT_ESCAPE,
        ).setDisableSubstitutionInValues(true)
        return substitutor.replace(template)
    }
}
