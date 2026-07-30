package com.otterworks.notification.consumer

import aws.sdk.kotlin.services.sqs.SqsClient
import aws.sdk.kotlin.services.sqs.model.DeleteMessageRequest
import aws.sdk.kotlin.services.sqs.model.ReceiveMessageRequest
import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.model.SqsNotificationMessage
import com.otterworks.notification.service.NotificationService
import io.micrometer.core.instrument.Counter
import io.micrometer.core.instrument.MeterRegistry
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import mu.KotlinLogging
import redis.clients.jedis.JedisPool
import redis.clients.jedis.JedisPoolConfig

private val logger = KotlinLogging.logger {}

// Lazy Redis pool for chaos flag checks.
private val redisPool: JedisPool by lazy {
    val host = System.getenv("REDIS_HOST") ?: "localhost"
    val port = System.getenv("REDIS_PORT")?.toIntOrNull() ?: 6379
    JedisPool(JedisPoolConfig(), host, port, 1000)
}

private fun chaosActive(flag: String): Boolean {
    return try {
        redisPool.resource.use { jedis -> jedis.exists(flag) }
    } catch (e: Exception) {
        false
    }
}

class SqsConsumer(
    private val sqsClient: SqsClient,
    private val notificationService: NotificationService,
    private val config: AppConfig,
    meterRegistry: MeterRegistry? = null,
) {
    private val processingErrorsCounter: Counter? =
        meterRegistry?.counter("notifications.processing.errors")
    // Standard lenient parser used in normal operation.
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }

    // CHAOS: strict parser that rejects messages whose timestamp field is not
    // a valid RFC 3339 string.  Legacy events emitted by older service versions
    // use Unix epoch integers for timestamps, which are rejected here.
    // When the chaos flag is active, every such message throws
    // SerializationException, is never deleted from the queue, and becomes
    // visible again after the SQS visibility timeout — causing queue depth to
    // climb indefinitely while the consumer appears healthy.
    private val strictJson = Json {
        ignoreUnknownKeys = false
        isLenient = false
    }

    suspend fun startPolling() = coroutineScope {
        logger.info { "Starting SQS consumer polling: ${config.sqsQueueUrl}" }

        while (isActive) {
            try {
                val request = ReceiveMessageRequest {
                    queueUrl = config.sqsQueueUrl
                    maxNumberOfMessages = config.sqsMaxMessages
                    waitTimeSeconds = config.sqsWaitTimeSeconds
                }

                val response = sqsClient.receiveMessage(request)
                val messages = response.messages ?: emptyList()

                if (messages.isNotEmpty()) {
                    logger.info { "Received ${messages.size} messages from SQS" }
                }

                for (msg in messages) {
                    launch {
                        try {
                            val body = msg.body ?: return@launch
                            val event = parseMessage(body)

                            if (event != null) {
                                notificationService.processEvent(event)

                                val deleteRequest = DeleteMessageRequest {
                                    queueUrl = config.sqsQueueUrl
                                    receiptHandle = msg.receiptHandle
                                }
                                sqsClient.deleteMessage(deleteRequest)
                                logger.debug { "Deleted SQS message: ${msg.messageId}" }
                            } else {
                                processingErrorsCounter?.increment()
                                logger.warn { "Failed to parse SQS message: ${msg.messageId}" }
                            }
                        } catch (e: Exception) {
                            logger.error(e) { "Error processing SQS message: ${msg.messageId}" }
                        }
                    }
                }

                if (messages.isEmpty()) {
                    delay(config.sqsPollIntervalMs)
                }
            } catch (e: Exception) {
                logger.error(e) { "Error polling SQS" }
                delay(config.sqsPollIntervalMs * 2)
            }
        }
    }

    internal fun parseMessage(body: String): SqsNotificationMessage? {
        val parser = if (chaosActive("chaos:notification-service:consumer_strict_schema")) strictJson else json
        return try {
            // Try parsing as direct message first
            parser.decodeFromString<SqsNotificationMessage>(body)
        } catch (_: Exception) {
            try {
                // Try unwrapping SNS envelope
                val snsWrapper = parser.decodeFromString<SnsEnvelope>(body)
                parser.decodeFromString<SqsNotificationMessage>(snsWrapper.Message)
            } catch (e: Exception) {
                logger.error(e) { "Failed to parse message body" }
                null
            }
        }
    }
}

@kotlinx.serialization.Serializable
internal data class SnsEnvelope(
    val Message: String,
    val MessageId: String = "",
    val TopicArn: String = "",
    val Type: String = "",
)
