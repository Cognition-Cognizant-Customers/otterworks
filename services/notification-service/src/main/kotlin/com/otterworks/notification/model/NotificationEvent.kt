package com.otterworks.notification.model

import kotlinx.serialization.Serializable

@Serializable
enum class EventType {
    file_shared,
    comment_added,
    document_edited,
    user_mentioned;

    companion object {
        fun fromString(value: String): EventType? = entries.find { it.name == value }
    }
}

@Serializable
data class NotificationEvent(
    val eventType: String,
    val sourceService: String = "",
    val userId: String,
    val actorId: String = "",
    val resourceId: String = "",
    val resourceType: String = "",
    val title: String = "",
    val message: String = "",
    val metadata: Map<String, String> = emptyMap(),
    @Serializable(with = FlexibleTimestampSerializer::class)
    val timestamp: String,
)

@Serializable
data class SqsNotificationMessage(
    val eventType: String,
    val fileId: String = "",
    val ownerId: String = "",
    val sharedWithUserId: String = "",
    val documentId: String = "",
    val commentId: String = "",
    val userId: String = "",
    val actorId: String = "",
    val mentionedUserId: String = "",
    @Serializable(with = FlexibleTimestampSerializer::class)
    val timestamp: String,
)

@Serializable
data class Notification(
    val id: String,
    val userId: String,
    val type: String,
    val title: String,
    val message: String,
    val resourceId: String = "",
    val resourceType: String = "",
    val actorId: String = "",
    val read: Boolean = false,
    val deliveredVia: List<String> = emptyList(),
    val createdAt: String,
)

@Serializable
enum class DeliveryChannel {
    EMAIL,
    IN_APP,
    PUSH;
}

@Serializable
data class NotificationPreference(
    val userId: String,
    val channels: Map<String, List<DeliveryChannel>> = mapOf(
        "file_shared" to listOf(DeliveryChannel.EMAIL, DeliveryChannel.IN_APP, DeliveryChannel.PUSH),
        "comment_added" to listOf(DeliveryChannel.IN_APP, DeliveryChannel.PUSH),
        "document_edited" to listOf(DeliveryChannel.IN_APP),
        "user_mentioned" to listOf(DeliveryChannel.EMAIL, DeliveryChannel.IN_APP, DeliveryChannel.PUSH),
    ),
)

@Serializable
data class PaginatedResponse<T>(
    val data: List<T>,
    val total: Int,
    val page: Int,
    val pageSize: Int,
    val hasMore: Boolean,
)

@Serializable
data class UnreadCountResponse(
    val userId: String,
    val unreadCount: Int,
)

@Serializable
data class NotificationPreferenceRequest(
    val userId: String,
    val eventType: String,
    val channels: List<DeliveryChannel>,
)
