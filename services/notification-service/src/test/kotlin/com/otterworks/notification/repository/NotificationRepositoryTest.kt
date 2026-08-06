package com.otterworks.notification.repository

import aws.sdk.kotlin.services.dynamodb.DynamoDbClient
import aws.sdk.kotlin.services.dynamodb.model.AttributeValue
import aws.sdk.kotlin.services.dynamodb.model.DeleteItemRequest
import aws.sdk.kotlin.services.dynamodb.model.DeleteItemResponse
import aws.sdk.kotlin.services.dynamodb.model.GetItemRequest
import aws.sdk.kotlin.services.dynamodb.model.GetItemResponse
import aws.sdk.kotlin.services.dynamodb.model.PutItemRequest
import aws.sdk.kotlin.services.dynamodb.model.PutItemResponse
import aws.sdk.kotlin.services.dynamodb.model.QueryRequest
import aws.sdk.kotlin.services.dynamodb.model.QueryResponse
import aws.sdk.kotlin.services.dynamodb.model.UpdateItemRequest
import aws.sdk.kotlin.services.dynamodb.model.UpdateItemResponse
import com.otterworks.notification.model.DeliveryChannel
import com.otterworks.notification.support.Fixtures
import com.otterworks.notification.support.RandomOrderRunner
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import org.junit.runner.RunWith
import kotlin.test.Ignore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

@RunWith(RandomOrderRunner::class)
class NotificationRepositoryTest {

    private fun item(
        id: String,
        userId: String = "user-a",
        read: Boolean = false,
    ): Map<String, AttributeValue> = mapOf(
        "id" to AttributeValue.S(id),
        "userId" to AttributeValue.S(userId),
        "type" to AttributeValue.S("file_shared"),
        "title" to AttributeValue.S("File Shared With You"),
        "message" to AttributeValue.S("msg"),
        "resourceId" to AttributeValue.S("file-1"),
        "resourceType" to AttributeValue.S("file"),
        "actorId" to AttributeValue.S("owner-1"),
        "read" to AttributeValue.Bool(read),
        "deliveredVia" to AttributeValue.L(listOf(AttributeValue.S("in_app"))),
        "createdAt" to AttributeValue.S(Fixtures.FIXED_TIMESTAMP),
    )

    private fun client(): DynamoDbClient = mockk(relaxed = true)

    private fun repository(client: DynamoDbClient) = NotificationRepository(client, Fixtures.config())

    // ── writes ────────────────────────────────────────────────────────────────

    @Test
    fun `saveNotification writes every field to the configured table`() = runTest {
        val client = client()
        val request = slot<PutItemRequest>()
        coEvery { client.putItem(capture(request)) } returns PutItemResponse {}

        repository(client).saveNotification(Fixtures.notification(id = "n-1", userId = "user-a"))

        assertEquals("test-notifications", request.captured.tableName)
        val written = request.captured.item!!
        assertEquals(AttributeValue.S("n-1"), written["id"])
        assertEquals(AttributeValue.S("user-a"), written["userId"])
        assertEquals(AttributeValue.Bool(false), written["read"])
        assertEquals(listOf(AttributeValue.S("in_app")), (written["deliveredVia"] as AttributeValue.L).value)
    }

    @Test
    fun `savePreferences serialises channels as a map of string lists`() = runTest {
        val client = client()
        val request = slot<PutItemRequest>()
        coEvery { client.putItem(capture(request)) } returns PutItemResponse {}

        repository(client).savePreferences(
            com.otterworks.notification.model.NotificationPreference(
                userId = "user-a",
                channels = mapOf("file_shared" to listOf(DeliveryChannel.EMAIL, DeliveryChannel.IN_APP)),
            )
        )

        assertEquals("test-preferences", request.captured.tableName)
        val channels = (request.captured.item!!["channels"] as AttributeValue.M).value
        assertEquals(
            listOf(AttributeValue.S("EMAIL"), AttributeValue.S("IN_APP")),
            (channels["file_shared"] as AttributeValue.L).value,
        )
    }

    // ── reads ─────────────────────────────────────────────────────────────────

    @Test
    fun `getNotificationById maps a stored item`() = runTest {
        val client = client()
        coEvery { client.getItem(any<GetItemRequest>()) } returns GetItemResponse { item = item("n-1") }

        val notification = repository(client).getNotificationById("n-1")

        assertEquals("n-1", notification?.id)
        assertEquals("user-a", notification?.userId)
        assertEquals(listOf("in_app"), notification?.deliveredVia)
    }

    @Test
    fun `getNotificationById returns null when the item is absent`() = runTest {
        val client = client()
        coEvery { client.getItem(any<GetItemRequest>()) } returns GetItemResponse {}

        assertNull(repository(client).getNotificationById("missing"))
    }

    @Test
    fun `getNotificationById skips an item that has no id attribute`() = runTest {
        val client = client()
        coEvery { client.getItem(any<GetItemRequest>()) } returns GetItemResponse {
            item = mapOf("userId" to AttributeValue.S("user-a"))
        }

        assertNull(repository(client).getNotificationById("n-1"))
    }

    @Test
    fun `getNotificationsByUserId follows every page of the query`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returnsMany listOf(
            QueryResponse {
                items = listOf(item("n-1"), item("n-2"))
                lastEvaluatedKey = mapOf("id" to AttributeValue.S("n-2"))
            },
            QueryResponse { items = listOf(item("n-3")) },
        )

        val (page, total) = repository(client).getNotificationsByUserId("user-a", page = 1, pageSize = 10)

        assertEquals(3, total)
        assertEquals(listOf("n-1", "n-2", "n-3"), page.map { it.id })
        coVerify(exactly = 2) { client.query(any<QueryRequest>()) }
    }

    @Test
    fun `getNotificationsByUserId returns the first page and the full total`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returns QueryResponse {
            items = listOf(item("n-1"), item("n-2"), item("n-3"))
        }

        val (page, total) = repository(client).getNotificationsByUserId("user-a", page = 1, pageSize = 2)

        assertEquals(listOf("n-1", "n-2"), page.map { it.id })
        assertEquals(3, total)
    }

    @Test
    fun `getNotificationsByUserId returns the trailing partial page`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returns QueryResponse {
            items = listOf(item("n-1"), item("n-2"), item("n-3"))
        }

        val (page, total) = repository(client).getNotificationsByUserId("user-a", page = 2, pageSize = 2)

        assertEquals(listOf("n-3"), page.map { it.id })
        assertEquals(3, total)
    }

    @Test
    fun `getNotificationsByUserId returns nothing past the last page`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returns QueryResponse {
            items = listOf(item("n-1"), item("n-2"), item("n-3"))
        }

        val (page, total) = repository(client).getNotificationsByUserId("user-a", page = 3, pageSize = 2)

        assertTrue(page.isEmpty())
        assertEquals(3, total)
    }

    @Test
    fun `getNotificationsByUserId currently throws for a page index below one`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returns QueryResponse { items = listOf(item("n-1")) }

        // (page - 1) * pageSize is negative, and List.drop rejects a negative count.
        assertFailsWith<IllegalArgumentException> {
            repository(client).getNotificationsByUserId("user-a", page = 0, pageSize = 2)
        }
    }

    @Ignore(
        "DEFECT: page indexes are never validated. GET /api/v1/notifications?page=0 (or any " +
            "negative page) reaches NotificationRepository.getNotificationsByUserId, which calls " +
            "List.drop with a negative count and throws IllegalArgumentException, surfacing as a " +
            "500 instead of a 400 or an empty page. Not fixed here: this package is test-only."
    )
    @Test
    fun `getNotificationsByUserId clamps a page index below one`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returns QueryResponse { items = listOf(item("n-1")) }

        val (page, _) = repository(client).getNotificationsByUserId("user-a", page = 0, pageSize = 2)

        assertEquals(listOf("n-1"), page.map { it.id })
    }

    @Test
    fun `getUnreadCount sums the counts of every query page`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returnsMany listOf(
            QueryResponse {
                count = 2
                lastEvaluatedKey = mapOf("id" to AttributeValue.S("n-2"))
            },
            QueryResponse { count = 1 },
        )

        assertEquals(3, repository(client).getUnreadCount("user-a"))
    }

    @Test
    fun `getUnreadCount is zero when the user has nothing unread`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returns QueryResponse { count = 0 }

        assertEquals(0, repository(client).getUnreadCount("user-a"))
    }

    @Test
    fun `getUnreadCount filters on the read flag of the queried user only`() = runTest {
        val client = client()
        val request = slot<QueryRequest>()
        coEvery { client.query(capture(request)) } returns QueryResponse { count = 1 }

        repository(client).getUnreadCount("user-a")

        assertEquals("userId = :uid", request.captured.keyConditionExpression)
        assertEquals(
            AttributeValue.S("user-a"),
            request.captured.expressionAttributeValues!![":uid"],
        )
        assertEquals(
            AttributeValue.Bool(false),
            request.captured.expressionAttributeValues!![":readVal"],
        )
    }

    // ── mutations ─────────────────────────────────────────────────────────────

    @Test
    fun `markAsRead reports success and guards on the item existing`() = runTest {
        val client = client()
        val request = slot<UpdateItemRequest>()
        coEvery { client.updateItem(capture(request)) } returns UpdateItemResponse {}

        assertTrue(repository(client).markAsRead("n-1"))
        assertEquals("attribute_exists(id)", request.captured.conditionExpression)
    }

    @Test
    fun `markAsRead reports failure when the update is rejected`() = runTest {
        val client = client()
        coEvery { client.updateItem(any<UpdateItemRequest>()) } throws RuntimeException("condition failed")

        assertFalse(repository(client).markAsRead("missing"))
    }

    @Test
    fun `markAllAsRead updates only the unread notifications`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returns QueryResponse {
            items = listOf(item("n-1", read = false), item("n-2", read = true), item("n-3", read = false))
        }
        coEvery { client.updateItem(any<UpdateItemRequest>()) } returns UpdateItemResponse {}

        assertEquals(2, repository(client).markAllAsRead("user-a"))
        coVerify(exactly = 2) { client.updateItem(any<UpdateItemRequest>()) }
    }

    @Test
    fun `markAllAsRead is a no-op when everything is already read`() = runTest {
        val client = client()
        coEvery { client.query(any<QueryRequest>()) } returns QueryResponse {
            items = listOf(item("n-1", read = true))
        }

        assertEquals(0, repository(client).markAllAsRead("user-a"))
        coVerify(exactly = 0) { client.updateItem(any<UpdateItemRequest>()) }
    }

    @Test
    fun `deleteNotification reports success`() = runTest {
        val client = client()
        val request = slot<DeleteItemRequest>()
        coEvery { client.deleteItem(capture(request)) } returns DeleteItemResponse {}

        assertTrue(repository(client).deleteNotification("n-1"))
        assertEquals(AttributeValue.S("n-1"), request.captured.key!!["id"])
    }

    @Test
    fun `deleteNotification reports failure when DynamoDB rejects the call`() = runTest {
        val client = client()
        coEvery { client.deleteItem(any<DeleteItemRequest>()) } throws RuntimeException("boom")

        assertFalse(repository(client).deleteNotification("n-1"))
    }

    // ── preferences ───────────────────────────────────────────────────────────

    @Test
    fun `getPreferences falls back to the defaults for an unknown user`() = runTest {
        val client = client()
        coEvery { client.getItem(any<GetItemRequest>()) } returns GetItemResponse {}

        val preferences = repository(client).getPreferences("user-a")

        assertEquals("user-a", preferences.userId)
        assertEquals(
            listOf(DeliveryChannel.EMAIL, DeliveryChannel.IN_APP, DeliveryChannel.PUSH),
            preferences.channels["file_shared"],
        )
    }

    @Test
    fun `getPreferences maps stored channels`() = runTest {
        val client = client()
        coEvery { client.getItem(any<GetItemRequest>()) } returns GetItemResponse {
            item = mapOf(
                "userId" to AttributeValue.S("user-a"),
                "channels" to AttributeValue.M(
                    mapOf("file_shared" to AttributeValue.L(listOf(AttributeValue.S("IN_APP"))))
                ),
            )
        }

        val preferences = repository(client).getPreferences("user-a")

        assertEquals(listOf(DeliveryChannel.IN_APP), preferences.channels["file_shared"])
    }

    @Test
    fun `getPreferences drops channel names it does not recognise`() = runTest {
        val client = client()
        coEvery { client.getItem(any<GetItemRequest>()) } returns GetItemResponse {
            item = mapOf(
                "userId" to AttributeValue.S("user-a"),
                "channels" to AttributeValue.M(
                    mapOf(
                        "file_shared" to AttributeValue.L(
                            listOf(AttributeValue.S("CARRIER_PIGEON"), AttributeValue.S("EMAIL"))
                        )
                    )
                ),
            )
        }

        assertEquals(
            listOf(DeliveryChannel.EMAIL),
            repository(client).getPreferences("user-a").channels["file_shared"],
        )
    }

    @Test
    fun `getPreferences keeps an explicit empty opt-out list`() = runTest {
        val client = client()
        coEvery { client.getItem(any<GetItemRequest>()) } returns GetItemResponse {
            item = mapOf(
                "userId" to AttributeValue.S("user-a"),
                "channels" to AttributeValue.M(mapOf("file_shared" to AttributeValue.L(emptyList()))),
            )
        }

        assertEquals(emptyList(), repository(client).getPreferences("user-a").channels["file_shared"])
    }

    @Test
    fun `getPreferences reads the preferences table keyed by user`() = runTest {
        val client = client()
        val request = slot<GetItemRequest>()
        coEvery { client.getItem(capture(request)) } returns GetItemResponse {}

        repository(client).getPreferences("user-a")

        assertEquals("test-preferences", request.captured.tableName)
        assertEquals(AttributeValue.S("user-a"), request.captured.key!!["userId"])
    }
}
