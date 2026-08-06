package com.otterworks.notification.routes

import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.configurePlugins
import com.otterworks.notification.model.DeliveryChannel
import com.otterworks.notification.model.Notification
import com.otterworks.notification.model.NotificationPreference
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.websocket.WebSocketManager
import io.ktor.client.request.delete
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
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.koin.core.context.stopKoin
import org.koin.dsl.module
import org.koin.ktor.plugin.Koin
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Ignore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * HTTP-level tests for `Routes.kt`, wired the same way `Application.module` wires it
 * (ContentNegotiation + StatusPages + WebSockets + Koin) but with the service layer mocked.
 *
 * The `... regression` cases below pin the fix for the `400 Bad Request` on
 * `GET /api/v1/notifications` and `GET /api/v1/notifications/unread-count` recorded in
 * `docs/exploratory-qa-report.md` → "Confirmed Bugs / High Priority / Notifications API
 * returns 400 everywhere". See that PR's description for the planted-vs-genuine judgement.
 */
class NotificationRoutesTest {

    private val config = AppConfig(
        port = 8086,
        awsRegion = "us-east-1",
        awsEndpointUrl = null,
        sqsQueueUrl = "http://localhost:4566/000000000000/test-queue",
        snsTopicArn = "arn:aws:sns:us-east-1:000000000000:test-topic",
        dynamoDbTableNotifications = "test-notifications",
        dynamoDbTablePreferences = "test-preferences",
        sesFromEmail = "test@otterworks.io",
        sqsPollIntervalMs = 1_000,
        sqsMaxMessages = 10,
        sqsWaitTimeSeconds = 5,
    )

    private val service = mockk<NotificationService>(relaxed = true)

    @BeforeTest
    fun resetKoin() = stopKoin()

    @AfterTest
    fun tearDownKoin() = stopKoin()

    private fun notification(id: String, read: Boolean = false) = Notification(
        id = id,
        userId = "user-1",
        type = "file_shared",
        title = "File Shared With You",
        message = "A file has been shared with you.",
        resourceId = "file-1",
        resourceType = "file",
        actorId = "actor-1",
        read = read,
        deliveredVia = listOf("in_app"),
        createdAt = "2024-01-01T00:00:00Z",
    )

    private fun routeTest(block: suspend ApplicationTestBuilder.() -> Unit) = testApplication {
        application {
            configurePlugins(config)
            install(Koin) {
                modules(
                    module {
                        single { service }
                        single { WebSocketManager() }
                    },
                )
            }
            configureRouting(PrometheusMeterRegistry(PrometheusConfig.DEFAULT))
        }
        block()
    }

    private suspend fun HttpResponse.json(): JsonObject =
        Json.parseToJsonElement(bodyAsText()).jsonObject

    // ── Regression: the 400 documented in docs/exploratory-qa-report.md ──────────

    @Test
    fun `GET notifications with the gateway X-User-ID header returns 200 not 400 - regression`() = routeTest {
        coEvery { service.getNotifications("user-1", 1, 20) } returns Pair(listOf(notification("n-1")), 1)

        val response = client.get("/api/v1/notifications") { header("X-User-ID", "user-1") }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = response.json()
        assertEquals(1, body["total"]!!.jsonPrimitive.int)
        assertEquals("n-1", body["data"]!!.jsonArray[0].jsonObject["id"]!!.jsonPrimitive.content)
    }

    @Test
    fun `GET unread-count with the gateway X-User-ID header returns 200 not 400 - regression`() = routeTest {
        coEvery { service.getUnreadCount("user-1") } returns 3

        val response = client.get("/api/v1/notifications/unread-count") { header("X-User-ID", "user-1") }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = response.json()
        assertEquals("user-1", body["userId"]!!.jsonPrimitive.content)
        assertEquals(3, body["unreadCount"]!!.jsonPrimitive.int)
    }

    @Test
    fun `PUT read-all with the gateway X-User-ID header returns 200 not 400 - regression`() = routeTest {
        coEvery { service.markAllAsRead("user-1") } returns 2

        val response = client.put("/api/v1/notifications/read-all") { header("X-User-ID", "user-1") }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(2, response.json()["markedCount"]!!.jsonPrimitive.int)
    }

    @Test
    fun `GET preferences with the gateway X-User-ID header returns 200 not 400 - regression`() = routeTest {
        coEvery { service.getPreferences("user-1") } returns NotificationPreference(
            userId = "user-1",
            channels = mapOf("file_shared" to listOf(DeliveryChannel.IN_APP)),
        )

        val response = client.get("/api/v1/preferences") { header("X-User-ID", "user-1") }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals("user-1", response.json()["userId"]!!.jsonPrimitive.content)
    }

    @Test
    fun `the user_id query parameter still works for non-gateway callers`() = routeTest {
        coEvery { service.getUnreadCount("user-7") } returns 0

        val response = client.get("/api/v1/notifications/unread-count?user_id=user-7")

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals("user-7", response.json()["userId"]!!.jsonPrimitive.content)
    }

    @Test
    fun `the X-User-ID header takes precedence over a conflicting user_id query parameter`() = routeTest {
        coEvery { service.getUnreadCount("header-user") } returns 1

        val response = client.get("/api/v1/notifications/unread-count?user_id=query-user") {
            header("X-User-ID", "header-user")
        }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals("header-user", response.json()["userId"]!!.jsonPrimitive.content)
        coVerify(exactly = 0) { service.getUnreadCount("query-user") }
    }

    // ── Negative: identity genuinely absent ─────────────────────────────────────

    @Test
    fun `GET notifications without any user identity is a 400`() = routeTest {
        val response = client.get("/api/v1/notifications")

        assertEquals(HttpStatusCode.BadRequest, response.status)
        assertTrue(response.json()["error"]!!.jsonPrimitive.content.contains("user_id is required"))
        coVerify(exactly = 0) { service.getNotifications(any(), any(), any()) }
    }

    @Test
    fun `GET unread-count without any user identity is a 400`() = routeTest {
        val response = client.get("/api/v1/notifications/unread-count")

        assertEquals(HttpStatusCode.BadRequest, response.status)
        coVerify(exactly = 0) { service.getUnreadCount(any()) }
    }

    @Test
    fun `a blank X-User-ID header is rejected rather than queried as a blank user`() = routeTest {
        val response = client.get("/api/v1/notifications/unread-count") { header("X-User-ID", "   ") }

        assertEquals(HttpStatusCode.BadRequest, response.status)
        coVerify(exactly = 0) { service.getUnreadCount(any()) }
    }

    @Test
    fun `an empty user_id query parameter is rejected`() = routeTest {
        val response = client.get("/api/v1/notifications?user_id=")

        assertEquals(HttpStatusCode.BadRequest, response.status)
        coVerify(exactly = 0) { service.getNotifications(any(), any(), any()) }
    }

    // ── Boundary trio on the hasMore threshold: page * page_size vs. total ───────

    @Test
    fun `hasMore is true when the page ends one row short of the total`() = routeTest {
        coEvery { service.getNotifications("user-1", 1, 2) } returns
            Pair(listOf(notification("n-1"), notification("n-2")), 3)

        val response = client.get("/api/v1/notifications?page=1&page_size=2") {
            header("X-User-ID", "user-1")
        }

        assertEquals(HttpStatusCode.OK, response.status)
        assertTrue(response.json()["hasMore"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `hasMore is false when the page ends exactly on the total`() = routeTest {
        coEvery { service.getNotifications("user-1", 1, 3) } returns
            Pair(listOf(notification("n-1"), notification("n-2"), notification("n-3")), 3)

        val response = client.get("/api/v1/notifications?page=1&page_size=3") {
            header("X-User-ID", "user-1")
        }

        assertEquals(false, response.json()["hasMore"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `hasMore is false when the page size overshoots the total`() = routeTest {
        coEvery { service.getNotifications("user-1", 1, 4) } returns
            Pair(listOf(notification("n-1"), notification("n-2"), notification("n-3")), 3)

        val response = client.get("/api/v1/notifications?page=1&page_size=4") {
            header("X-User-ID", "user-1")
        }

        assertEquals(false, response.json()["hasMore"]!!.jsonPrimitive.boolean)
        assertEquals(3, response.json()["data"]!!.jsonArray.size)
    }

    @Test
    fun `unparseable pagination parameters fall back to page 1 and page size 20`() = routeTest {
        coEvery { service.getNotifications("user-1", 1, 20) } returns Pair(emptyList(), 0)

        val response = client.get("/api/v1/notifications?page=abc&page_size=xyz") {
            header("X-User-ID", "user-1")
        }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = response.json()
        assertEquals(1, body["page"]!!.jsonPrimitive.int)
        assertEquals(20, body["pageSize"]!!.jsonPrimitive.int)
        coVerify(exactly = 1) { service.getNotifications("user-1", 1, 20) }
    }

    @Test
    fun `the camelCase pageSize parameter sent by the web client is ignored`() = routeTest {
        // FINDING (client/server contract drift, out of this package's scope):
        // frontend/client-app/src/lib/api.ts sends `page`/`pageSize`, but the route reads
        // `page_size`. The camelCase value is silently dropped and the default of 20 is used.
        // Pinning the server side here so a fix on either side is a deliberate, visible change.
        coEvery { service.getNotifications("user-1", 1, 20) } returns Pair(emptyList(), 0)

        val response = client.get("/api/v1/notifications?page=1&pageSize=5") {
            header("X-User-ID", "user-1")
        }

        assertEquals(20, response.json()["pageSize"]!!.jsonPrimitive.int)
        coVerify(exactly = 1) { service.getNotifications("user-1", 1, 20) }
    }

    @Test
    fun `page zero is forwarded to the service without validation`() = routeTest {
        // FINDING (genuine, unfixed): the route does not validate the pagination window, so
        // `page=0` reaches NotificationRepository.getNotificationsByUserId, where
        // `allItems.drop((page - 1) * pageSize)` throws on a negative count — see the
        // disabled `page zero should not be forwarded ...` case in NotificationRepositoryTest.
        coEvery { service.getNotifications("user-1", 0, 20) } returns Pair(emptyList(), 0)

        val response = client.get("/api/v1/notifications?page=0") { header("X-User-ID", "user-1") }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(0, response.json()["page"]!!.jsonPrimitive.int)
        coVerify(exactly = 1) { service.getNotifications("user-1", 0, 20) }
    }

    // ── Single-notification routes ──────────────────────────────────────────────

    @Test
    fun `GET notification by id returns the notification`() = routeTest {
        coEvery { service.getNotificationById("n-1") } returns notification("n-1")

        val response = client.get("/api/v1/notifications/n-1")

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals("n-1", response.json()["id"]!!.jsonPrimitive.content)
    }

    @Test
    fun `GET an unknown notification id is a 404`() = routeTest {
        coEvery { service.getNotificationById("missing") } returns null

        val response = client.get("/api/v1/notifications/missing")

        assertEquals(HttpStatusCode.NotFound, response.status)
    }

    @Test
    fun `PUT read on an existing notification is a 204`() = routeTest {
        coEvery { service.markAsRead("n-1") } returns true

        val response = client.put("/api/v1/notifications/n-1/read")

        assertEquals(HttpStatusCode.NoContent, response.status)
    }

    @Test
    fun `PUT read on an unknown notification is a 404`() = routeTest {
        coEvery { service.markAsRead("missing") } returns false

        val response = client.put("/api/v1/notifications/missing/read")

        assertEquals(HttpStatusCode.NotFound, response.status)
    }

    @Test
    fun `DELETE an existing notification is a 204`() = routeTest {
        coEvery { service.deleteNotification("n-1") } returns true

        val response = client.delete("/api/v1/notifications/n-1")

        assertEquals(HttpStatusCode.NoContent, response.status)
    }

    @Test
    fun `DELETE an unknown notification is a 404`() = routeTest {
        coEvery { service.deleteNotification("missing") } returns false

        val response = client.delete("/api/v1/notifications/missing")

        assertEquals(HttpStatusCode.NotFound, response.status)
    }

    // ── Authorization: per-notification routes carry no ownership check ─────────

    @Test
    fun `GET notification by id ignores the caller identity`() = routeTest {
        // Pins today's behaviour: /{id}, /{id}/read and DELETE /{id} never look at
        // X-User-ID, so the row of another user is returned in full. See the disabled
        // cases below for the behaviour this should have.
        coEvery { service.getNotificationById("n-1") } returns notification("n-1")

        val response = client.get("/api/v1/notifications/n-1") { header("X-User-ID", "someone-else") }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals("user-1", response.json()["userId"]!!.jsonPrimitive.content)
    }

    @Test
    fun `DELETE a notification ignores the caller identity`() = routeTest {
        coEvery { service.deleteNotification("n-1") } returns true

        val response = client.delete("/api/v1/notifications/n-1") { header("X-User-ID", "someone-else") }

        assertEquals(HttpStatusCode.NoContent, response.status)
        coVerify(exactly = 1) { service.deleteNotification("n-1") }
    }

    @Test
    @Ignore(
        "DEFECT (genuine, not planted): GET /api/v1/notifications/{id} performs no ownership " +
            "check — it reads by id only, so any caller reaching the service can read any " +
            "user's notification (IDOR). The service also trusts X-User-ID unconditionally; the " +
            "gateway sets it from JWT claims but nothing here verifies it. See " +
            "docs/TEST-COVERAGE-EXPANSION-SOW.md §3 'Cross-cutting gaps — Authorization matrix'. " +
            "Test-only package: not fixing the production code here.",
    )
    fun `GET notification by id should not return another users notification`() = routeTest {
        coEvery { service.getNotificationById("n-1") } returns notification("n-1")

        val response = client.get("/api/v1/notifications/n-1") { header("X-User-ID", "someone-else") }

        assertEquals(HttpStatusCode.NotFound, response.status)
    }

    @Test
    @Ignore(
        "DEFECT (genuine, not planted): DELETE /api/v1/notifications/{id} performs no ownership " +
            "check, so any caller can delete another user's notification. Same root cause as " +
            "the disabled GET case above. Test-only package: not fixing the production code here.",
    )
    fun `DELETE should not remove another users notification`() = routeTest {
        coEvery { service.getNotificationById("n-1") } returns notification("n-1")

        val response = client.delete("/api/v1/notifications/n-1") { header("X-User-ID", "someone-else") }

        assertEquals(HttpStatusCode.NotFound, response.status)
        coVerify(exactly = 0) { service.deleteNotification(any()) }
    }

    @Test
    fun `an unhandled service failure is mapped to a 500 by StatusPages`() = routeTest {
        coEvery { service.getUnreadCount("user-1") } throws RuntimeException("dynamo down")

        val response = client.get("/api/v1/notifications/unread-count") { header("X-User-ID", "user-1") }

        assertEquals(HttpStatusCode.InternalServerError, response.status)
        assertEquals("Internal server error", response.json()["error"]!!.jsonPrimitive.content)
    }

    @Test
    fun `the health endpoint needs no user identity`() = routeTest {
        val response = client.get("/health")

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals("healthy", response.json()["status"]!!.jsonPrimitive.content)
    }
}
