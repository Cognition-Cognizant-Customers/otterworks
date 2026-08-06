package com.otterworks.notification.routes

import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.configurePlugins
import com.otterworks.notification.model.Notification
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.websocket.WebSocketManager
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.put
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.install
import io.ktor.server.testing.ApplicationTestBuilder
import io.ktor.server.testing.testApplication
import io.micrometer.prometheus.PrometheusConfig
import io.micrometer.prometheus.PrometheusMeterRegistry
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.koin.dsl.module
import org.koin.ktor.plugin.KoinIsolated
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * HTTP-level tests for the notification routes.
 *
 * The [NotificationService] is mocked so the assertions describe the contract
 * the routing layer exposes: status codes, response shape, and the pagination
 * values it accepts and echoes back.
 */
class RoutesTest {

    private val testConfig = AppConfig(
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

    private fun notification(id: String, userId: String = USER_ID, read: Boolean = false) = Notification(
        id = id,
        userId = userId,
        type = "file_shared",
        title = "File Shared With You",
        message = "A file was shared with you",
        read = read,
        createdAt = "2024-01-01T00:00:00Z",
    )

    private fun ApplicationTestBuilder.withRoutes(service: NotificationService) {
        application {
            configurePlugins(testConfig)
            install(KoinIsolated) {
                modules(
                    module {
                        single { service }
                        single { WebSocketManager() }
                    }
                )
            }
            configureRouting(PrometheusMeterRegistry(PrometheusConfig.DEFAULT))
        }
    }

    private suspend fun HttpResponse.json() = Json.parseToJsonElement(bodyAsText()).jsonObject

    // --- page_size handling -------------------------------------------------

    @Test
    fun `GET notifications with page_size zero is accepted unclamped`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, 0) } returns Pair(emptyList(), 7)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page_size=0") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = response.json()
        assertEquals(0, body["pageSize"]!!.jsonPrimitive.int)
        assertEquals(0, body["data"]!!.jsonArray.size)
        // page * pageSize == 0 < total, so the response advertises another page forever.
        assertTrue(body["hasMore"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `GET notifications with negative page_size is accepted unclamped`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, -1) } returns Pair(emptyList(), 7)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page_size=-1") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(-1, response.json()["pageSize"]!!.jsonPrimitive.int)
    }

    @Test
    fun `GET notifications with very large page_size is accepted unclamped`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, 1_000_000) } returns Pair(emptyList(), 7)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page_size=1000000") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(1_000_000, response.json()["pageSize"]!!.jsonPrimitive.int)
    }

    @Test
    fun `GET notifications with non-numeric page_size falls back to twenty`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, 20) } returns Pair(emptyList(), 0)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page_size=not-a-number") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(20, response.json()["pageSize"]!!.jsonPrimitive.int)
    }

    @Test
    fun `GET notifications with no page_size defaults to twenty`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, 20) } returns Pair(emptyList(), 0)
        withRoutes(service)

        val response = client.get("/api/v1/notifications") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = response.json()
        assertEquals(20, body["pageSize"]!!.jsonPrimitive.int)
        assertEquals(1, body["page"]!!.jsonPrimitive.int)
    }

    @Test
    fun `GET notifications with non-numeric page falls back to one`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, 20) } returns Pair(emptyList(), 0)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page=not-a-number") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(1, response.json()["page"]!!.jsonPrimitive.int)
    }

    // --- hasMore boundaries -------------------------------------------------

    @Test
    fun `GET notifications on the last page reports hasMore false`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 2, 10) } returns Pair(listOf(notification("n-11")), 20)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page=2&page_size=10") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertFalse(response.json()["hasMore"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `GET notifications one page past the last reports hasMore false with no data`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 3, 10) } returns Pair(emptyList(), 20)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page=3&page_size=10") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = response.json()
        assertFalse(body["hasMore"]!!.jsonPrimitive.boolean)
        assertEquals(0, body["data"]!!.jsonArray.size)
        assertEquals(20, body["total"]!!.jsonPrimitive.int)
    }

    @Test
    fun `GET notifications when total equals page times pageSize reports hasMore false`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, 20) } returns Pair(listOf(notification("n-1")), 20)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page=1&page_size=20") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertFalse(response.json()["hasMore"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `GET notifications with results remaining reports hasMore true`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, 10) } returns Pair(listOf(notification("n-1")), 25)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?page=1&page_size=10") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertTrue(response.json()["hasMore"]!!.jsonPrimitive.boolean)
    }

    // --- user identity ------------------------------------------------------

    @Test
    fun `GET notifications without a user id returns 400`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        withRoutes(service)

        val response = client.get("/api/v1/notifications")

        assertEquals(HttpStatusCode.BadRequest, response.status)
        assertTrue(response.json().containsKey("error"))
    }

    @Test
    fun `GET unread-count without a user id returns 400`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        withRoutes(service)

        val response = client.get("/api/v1/notifications/unread-count")

        assertEquals(HttpStatusCode.BadRequest, response.status)
        assertTrue(response.json().containsKey("error"))
    }

    @Test
    fun `PUT read-all without a user id returns 400`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        withRoutes(service)

        val response = client.put("/api/v1/notifications/read-all")

        assertEquals(HttpStatusCode.BadRequest, response.status)
        assertTrue(response.json().containsKey("error"))
    }

    @Test
    fun `GET notifications with a blank user id header returns 400`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        withRoutes(service)

        val response = client.get("/api/v1/notifications") { header(USER_HEADER, "   ") }

        assertEquals(HttpStatusCode.BadRequest, response.status)
    }

    @Test
    fun `GET unread-count with a blank user id query parameter returns 400`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        withRoutes(service)

        val response = client.get("/api/v1/notifications/unread-count?user_id=%20%20")

        assertEquals(HttpStatusCode.BadRequest, response.status)
    }

    @Test
    fun `PUT read-all with a blank user id header returns 400`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        withRoutes(service)

        val response = client.put("/api/v1/notifications/read-all") { header(USER_HEADER, " ") }

        assertEquals(HttpStatusCode.BadRequest, response.status)
    }

    @Test
    fun `GET notifications accepts the user id from the query parameter`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotifications(USER_ID, 1, 20) } returns Pair(emptyList(), 0)
        withRoutes(service)

        val response = client.get("/api/v1/notifications?user_id=$USER_ID")

        assertEquals(HttpStatusCode.OK, response.status)
    }

    // --- single notification lookups ---------------------------------------

    @Test
    fun `GET notification by unknown id returns 404`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotificationById("does-not-exist") } returns null
        withRoutes(service)

        val response = client.get("/api/v1/notifications/does-not-exist") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.NotFound, response.status)
        assertTrue(response.json().containsKey("error"))
    }

    @Test
    fun `GET notification by known id returns the notification`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getNotificationById("n-1") } returns notification("n-1")
        withRoutes(service)

        val response = client.get("/api/v1/notifications/n-1") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals("n-1", response.json()["id"]!!.jsonPrimitive.content)
    }

    @Test
    fun `PUT read on unknown id returns 404`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.markAsRead("does-not-exist") } returns false
        withRoutes(service)

        val response = client.put("/api/v1/notifications/does-not-exist/read") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.NotFound, response.status)
    }

    @Test
    fun `PUT read twice on the same notification returns 204 both times`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.markAsRead("n-1") } returns true
        withRoutes(service)

        val first = client.put("/api/v1/notifications/n-1/read") { header(USER_HEADER, USER_ID) }
        val second = client.put("/api/v1/notifications/n-1/read") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.NoContent, first.status)
        assertEquals(HttpStatusCode.NoContent, second.status)
    }

    @Test
    fun `GET unread-count for a user with no notifications returns zero`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.getUnreadCount("user-without-notifications") } returns 0
        withRoutes(service)

        val response = client.get("/api/v1/notifications/unread-count") {
            header(USER_HEADER, "user-without-notifications")
        }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = response.json()
        assertEquals(0, body["unreadCount"]!!.jsonPrimitive.int)
        assertEquals("user-without-notifications", body["userId"]!!.jsonPrimitive.content)
    }

    @Test
    fun `PUT read-all for a user with no unread notifications returns zero marked`() = testApplication {
        val service = mockk<NotificationService>(relaxed = true)
        coEvery { service.markAllAsRead(USER_ID) } returns 0
        withRoutes(service)

        val response = client.put("/api/v1/notifications/read-all") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(0, response.json()["markedCount"]!!.jsonPrimitive.int)
    }

    companion object {
        private const val USER_ID = "user-1"
        private const val USER_HEADER = "X-User-ID"
    }
}
