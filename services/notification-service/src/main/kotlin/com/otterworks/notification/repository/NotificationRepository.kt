package com.otterworks.notification.repository

import aws.sdk.kotlin.services.dynamodb.DynamoDbClient
import aws.sdk.kotlin.services.dynamodb.model.AttributeValue
import aws.sdk.kotlin.services.dynamodb.model.DeleteItemRequest
import aws.sdk.kotlin.services.dynamodb.model.GetItemRequest
import aws.sdk.kotlin.services.dynamodb.model.PutItemRequest
import aws.sdk.kotlin.services.dynamodb.model.QueryRequest
import aws.sdk.kotlin.services.dynamodb.model.UpdateItemRequest
import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.model.DeliveryChannel
import com.otterworks.notification.model.Notification
import com.otterworks.notification.model.NotificationPreference
import io.github.oshai.kotlinlogging.KotlinLogging

private val logger = KotlinLogging.logger {}

class NotificationRepository(
    private val dynamoDbClient: DynamoDbClient,
    private val config: AppConfig,
) {

    suspend fun saveNotification(notification: Notification) {
        val item = mutableMapOf<String, AttributeValue>(
            "id" to AttributeValue.S(notification.id),
            "userId" to AttributeValue.S(notification.userId),
            "type" to AttributeValue.S(notification.type),
            "title" to AttributeValue.S(notification.title),
            "message" to AttributeValue.S(notification.message),
            "resourceId" to AttributeValue.S(notification.resourceId),
            "resourceType" to AttributeValue.S(notification.resourceType),
            "actorId" to AttributeValue.S(notification.actorId),
            "read" to AttributeValue.Bool(notification.read),
            "deliveredVia" to AttributeValue.L(
                notification.deliveredVia.map { AttributeValue.S(it) }
            ),
            "createdAt" to AttributeValue.S(notification.createdAt),
        )

        val request = PutItemRequest {
            tableName = config.dynamoDbTableNotifications
            this.item = item
        }

        dynamoDbClient.putItem(request)
        logger.debug { "Saved notification ${notification.id} for user ${notification.userId}" }
    }

    suspend fun getNotificationById(id: String): Notification? {
        val request = GetItemRequest {
            tableName = config.dynamoDbTableNotifications
            key = mapOf("id" to AttributeValue.S(id))
        }

        val response = dynamoDbClient.getItem(request)
        return response.item?.let { mapToNotification(it) }
    }

    suspend fun getNotificationsByUserId(
        userId: String,
        page: Int = 1,
        pageSize: Int = 20,
    ): Pair<List<Notification>, Int> {
        val allItems = mutableListOf<Notification>()
        var lastEvaluatedKey: Map<String, AttributeValue>? = null

        do {
            val request = QueryRequest {
                tableName = config.dynamoDbTableNotifications
                indexName = "userId-createdAt-index"
                keyConditionExpression = "userId = :uid"
                expressionAttributeValues = mapOf(
                    ":uid" to AttributeValue.S(userId),
                )
                scanIndexForward = false
                if (lastEvaluatedKey != null) {
                    exclusiveStartKey = lastEvaluatedKey
                }
            }

            val response = dynamoDbClient.query(request)
            response.items?.mapNotNull { mapToNotification(it) }?.let { allItems.addAll(it) }
            lastEvaluatedKey = response.lastEvaluatedKey
        } while (lastEvaluatedKey != null)

        val total = allItems.size
        val startIndex = (page - 1) * pageSize
        val paged = allItems.drop(startIndex).take(pageSize)

        return Pair(paged, total)
    }

    suspend fun getUnreadCount(userId: String): Int {
        var totalCount = 0
        var lastEvaluatedKey: Map<String, AttributeValue>? = null

        do {
            val request = QueryRequest {
                tableName = config.dynamoDbTableNotifications
                indexName = "userId-createdAt-index"
                keyConditionExpression = "userId = :uid"
                filterExpression = "#r = :readVal"
                expressionAttributeNames = mapOf("#r" to "read")
                expressionAttributeValues = mapOf(
                    ":uid" to AttributeValue.S(userId),
                    ":readVal" to AttributeValue.Bool(false),
                )
                if (lastEvaluatedKey != null) {
                    exclusiveStartKey = lastEvaluatedKey
                }
            }

            val response = dynamoDbClient.query(request)
            totalCount += response.count
            lastEvaluatedKey = response.lastEvaluatedKey
        } while (lastEvaluatedKey != null)

        return totalCount
    }

    suspend fun markAsRead(id: String): Boolean {
        val request = UpdateItemRequest {
            tableName = config.dynamoDbTableNotifications
            key = mapOf("id" to AttributeValue.S(id))
            updateExpression = "SET #r = :readVal"
            expressionAttributeNames = mapOf("#r" to "read")
            expressionAttributeValues = mapOf(
                ":readVal" to AttributeValue.Bool(true),
            )
            conditionExpression = "attribute_exists(id)"
        }

        return try {
            dynamoDbClient.updateItem(request)
            logger.debug { "Marked notification $id as read" }
            true
        } catch (e: Exception) {
            logger.warn { "Failed to mark notification $id as read: ${e.message}" }
            false
        }
    }

    suspend fun markAllAsRead(userId: String): Int {
        val (notifications, _) = getNotificationsByUserId(userId, page = 1, pageSize = Int.MAX_VALUE)
        val unreadNotifications = notifications.filter { !it.read }

        for (notification in unreadNotifications) {
            markAsRead(notification.id)
        }

        logger.info { "Marked ${unreadNotifications.size} notifications as read for user $userId" }
        return unreadNotifications.size
    }

    suspend fun deleteNotification(id: String): Boolean {
        val request = DeleteItemRequest {
            tableName = config.dynamoDbTableNotifications
            key = mapOf("id" to AttributeValue.S(id))
        }

        return try {
            dynamoDbClient.deleteItem(request)
            logger.debug { "Deleted notification $id" }
            true
        } catch (e: Exception) {
            logger.warn { "Failed to delete notification $id: ${e.message}" }
            false
        }
    }

    suspend fun getPreferences(userId: String): NotificationPreference {
        val request = GetItemRequest {
            tableName = config.dynamoDbTablePreferences
            key = mapOf("userId" to AttributeValue.S(userId))
        }

        val response = dynamoDbClient.getItem(request)
        return response.item?.let { mapToPreference(it) }
            ?: NotificationPreference(userId = userId)
    }

    suspend fun savePreferences(preference: NotificationPreference) {
        val channelsMap = preference.channels.map { (eventType, channels) ->
            eventType to AttributeValue.L(channels.map { AttributeValue.S(it.name) })
        }.toMap()

        val item = mapOf(
            "userId" to AttributeValue.S(preference.userId),
            "channels" to AttributeValue.M(channelsMap),
        )

        val request = PutItemRequest {
            tableName = config.dynamoDbTablePreferences
            this.item = item
        }

        dynamoDbClient.putItem(request)
        logger.debug { "Saved preferences for user ${preference.userId}" }
    }

    private fun mapToNotification(item: Map<String, AttributeValue>): Notification? {
        return try {
            Notification(
                id = (item["id"] as? AttributeValue.S)?.value ?: return null,
                userId = (item["userId"] as? AttributeValue.S)?.value ?: return null,
                type = (item["type"] as? AttributeValue.S)?.value ?: "",
                title = (item["title"] as? AttributeValue.S)?.value ?: "",
                message = (item["message"] as? AttributeValue.S)?.value ?: "",
                resourceId = (item["resourceId"] as? AttributeValue.S)?.value ?: "",
                resourceType = (item["resourceType"] as? AttributeValue.S)?.value ?: "",
                actorId = (item["actorId"] as? AttributeValue.S)?.value ?: "",
                read = (item["read"] as? AttributeValue.Bool)?.value ?: false,
                deliveredVia = (item["deliveredVia"] as? AttributeValue.L)?.value?.mapNotNull {
                    (it as? AttributeValue.S)?.value
                } ?: emptyList(),
                createdAt = (item["createdAt"] as? AttributeValue.S)?.value ?: "",
            )
        } catch (e: Exception) {
            logger.error(e) { "Failed to map DynamoDB item to Notification" }
            null
        }
    }

    private fun mapToPreference(item: Map<String, AttributeValue>): NotificationPreference? {
        return try {
            val userId = (item["userId"] as? AttributeValue.S)?.value ?: return null
            val channelsMap = (item["channels"] as? AttributeValue.M)?.value

            val channels = channelsMap?.mapValues { (_, value) ->
                (value as? AttributeValue.L)?.value?.mapNotNull {
                    val name = (it as? AttributeValue.S)?.value ?: return@mapNotNull null
                    try {
                        DeliveryChannel.valueOf(name)
                    } catch (_: IllegalArgumentException) {
                        null
                    }
                } ?: emptyList()
            } ?: emptyMap()

            NotificationPreference(userId = userId, channels = channels)
        } catch (e: Exception) {
            logger.error(e) { "Failed to map DynamoDB item to NotificationPreference" }
            null
        }
    }
}
