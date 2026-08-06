package com.otterworks.notification.repository

import aws.sdk.kotlin.services.dynamodb.DynamoDbClient
import aws.sdk.kotlin.services.dynamodb.model.AttributeValue
import aws.sdk.kotlin.services.dynamodb.model.QueryRequest
import aws.sdk.kotlin.services.dynamodb.model.QueryResponse
import com.otterworks.notification.config.AppConfig
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

/**
 * Pagination behaviour of the notification listing query.
 *
 * The HTTP layer passes `page` and `page_size` straight through without a
 * floor or a ceiling, so these tests pin what the storage layer does with the
 * values a client can actually send.
 */
class NotificationPaginationTest {

    private val dynamoDbClient = mockk<DynamoDbClient>()

    private val config = AppConfig(
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

    private val repository = NotificationRepository(dynamoDbClient, config)

    private fun item(id: String) = mapOf(
        "id" to AttributeValue.S(id),
        "userId" to AttributeValue.S(USER_ID),
        "type" to AttributeValue.S("file_shared"),
        "title" to AttributeValue.S("File Shared With You"),
        "message" to AttributeValue.S("A file was shared with you"),
        "read" to AttributeValue.Bool(false),
        "createdAt" to AttributeValue.S("2024-01-01T00:00:00Z"),
    )

    private fun stubQueryWith(count: Int) {
        val response = QueryResponse {
            items = (1..count).map { item("n-$it") }
            this.count = count
            lastEvaluatedKey = null
        }
        coEvery { dynamoDbClient.query(any<QueryRequest>()) } returns response
    }

    @Test
    fun `getNotificationsByUserId returns the requested slice and the full total`() = runTest {
        stubQueryWith(25)

        val (page, total) = repository.getNotificationsByUserId(USER_ID, page = 2, pageSize = 10)

        assertEquals(25, total)
        assertEquals(10, page.size)
        assertEquals("n-11", page.first().id)
    }

    @Test
    fun `getNotificationsByUserId with pageSize zero returns an empty page`() = runTest {
        stubQueryWith(25)

        val (page, total) = repository.getNotificationsByUserId(USER_ID, page = 1, pageSize = 0)

        assertEquals(25, total)
        assertEquals(0, page.size)
    }

    @Test
    fun `getNotificationsByUserId with a huge pageSize returns every item`() = runTest {
        stubQueryWith(25)

        val (page, total) = repository.getNotificationsByUserId(USER_ID, page = 1, pageSize = 1_000_000)

        assertEquals(25, total)
        assertEquals(25, page.size)
    }

    @Test
    fun `getNotificationsByUserId past the last page returns an empty page`() = runTest {
        stubQueryWith(25)

        val (page, total) = repository.getNotificationsByUserId(USER_ID, page = 99, pageSize = 10)

        assertEquals(25, total)
        assertEquals(0, page.size)
    }

    @Test
    fun `getNotificationsByUserId with a negative pageSize fails`() = runTest {
        stubQueryWith(25)

        assertFailsWith<IllegalArgumentException> {
            repository.getNotificationsByUserId(USER_ID, page = 1, pageSize = -1)
        }
    }

    @Test
    fun `getNotificationsByUserId with page zero fails`() = runTest {
        stubQueryWith(25)

        assertFailsWith<IllegalArgumentException> {
            repository.getNotificationsByUserId(USER_ID, page = 0, pageSize = 20)
        }
    }

    companion object {
        private const val USER_ID = "user-1"
    }
}
