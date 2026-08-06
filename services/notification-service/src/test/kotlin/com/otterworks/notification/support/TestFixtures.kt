package com.otterworks.notification.support

import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.model.Notification
import com.otterworks.notification.model.SqsNotificationMessage

/**
 * Fixture factories. Every helper returns a freshly built value so no test can
 * mutate state observed by another test.
 */
object Fixtures {

    const val FIXED_TIMESTAMP = "2024-01-01T00:00:00Z"

    fun config(): AppConfig = AppConfig(
        port = 8086,
        awsRegion = "us-east-1",
        awsEndpointUrl = null,
        sqsQueueUrl = "http://localhost:4566/000000000000/test-queue",
        snsTopicArn = "arn:aws:sns:us-east-1:000000000000:test-topic",
        dynamoDbTableNotifications = "test-notifications",
        dynamoDbTablePreferences = "test-preferences",
        sesFromEmail = "test@otterworks.io",
        sqsPollIntervalMs = 1000,
        sqsMaxMessages = 10,
        sqsWaitTimeSeconds = 5,
    )

    fun fileSharedEvent(
        fileId: String = "file-1",
        ownerId: String = "owner-1",
        sharedWithUserId: String = "user-a",
    ): SqsNotificationMessage = SqsNotificationMessage(
        eventType = "file_shared",
        fileId = fileId,
        ownerId = ownerId,
        sharedWithUserId = sharedWithUserId,
        timestamp = FIXED_TIMESTAMP,
    )

    fun commentAddedEvent(
        userId: String = "user-a",
        actorId: String = "actor-1",
        documentId: String = "doc-1",
        commentId: String = "comment-1",
    ): SqsNotificationMessage = SqsNotificationMessage(
        eventType = "comment_added",
        userId = userId,
        actorId = actorId,
        documentId = documentId,
        commentId = commentId,
        timestamp = FIXED_TIMESTAMP,
    )

    fun fileSharedEventJson(
        fileId: String = "file-1",
        ownerId: String = "owner-1",
        sharedWithUserId: String = "user-a",
    ): String = """
        {"eventType":"file_shared","fileId":"$fileId","ownerId":"$ownerId",
         "sharedWithUserId":"$sharedWithUserId","timestamp":"$FIXED_TIMESTAMP"}
    """.trimIndent()

    fun notification(
        id: String = "n-1",
        userId: String = "user-a",
        read: Boolean = false,
        type: String = "file_shared",
    ): Notification = Notification(
        id = id,
        userId = userId,
        type = type,
        title = "File Shared With You",
        message = "A file has been shared with you by user owner-1.",
        resourceId = "file-1",
        resourceType = "file",
        actorId = "owner-1",
        read = read,
        deliveredVia = listOf("in_app"),
        createdAt = FIXED_TIMESTAMP,
    )

    fun notifications(count: Int, userId: String = "user-a", read: Boolean = false): List<Notification> =
        (1..count).map { notification(id = "n-$it", userId = userId, read = read) }
}
