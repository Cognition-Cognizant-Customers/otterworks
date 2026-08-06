package com.otterworks.notification.routes

import com.otterworks.notification.configurePlugins
import com.otterworks.notification.model.DeliveryChannel
import com.otterworks.notification.model.NotificationPreference
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.support.Fixtures
import com.otterworks.notification.support.RandomOrderRunner
import com.otterworks.notification.websocket.WebSocketManager
import io.ktor.client.request.delete
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.patch
import io.ktor.client.request.post
import io.ktor.client.request.put
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import io.ktor.server.application.install
import io.ktor.server.testing.ApplicationTestBuilder
import io.ktor.server.testing.testApplication
import io.micrometer.prometheus.PrometheusConfig
import io.micrometer.prometheus.PrometheusMeterRegistry
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.runner.RunWith
import org.koin.core.context.stopKoin
import org.koin.dsl.module
import org.koin.ktor.plugin.Koin
import kotlin.test.Ignore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * HTTP-level tests for the notification routes.
 *
 * Includes the regression pair for the live 400 documented in `docs/exploratory-qa-report.md`
 * ("Notifications API returns 400 everywhere"): one test pins today's 400, and the matching
 * `@Ignore`d test states the behaviour a fix must produce.
 */
@RunWith(RandomOrderRunner::class)
class NotificationRoutesTest {

    private val json = Json { ignoreUnknownKeys = true }

    @After
    fun tearDown() {
        runCatching { stopKoin() }
    }

    private fun ApplicationTestBuilder.notificationApp(service: NotificationService) {
        application {
            install(Koin) {
                modules(
                    module {
                        single<NotificationService> { service }
                        single { WebSocketManager() }
                    }
                )
            }
            configurePlugins(Fixtures.config())
            configureRouting(PrometheusMeterRegistry(PrometheusConfig.DEFAULT))
        }
    }

    private suspend fun HttpResponse.jsonBody(): JsonObject =
        json.parseToJsonElement(bodyAsText()) as JsonObject

    private fun service(): NotificationService = mockk(relaxed = true)

    // ── list: happy path and pagination boundaries ────────────────────────────

    @Test
    fun `list returns 200 and a paginated payload for an identified user`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 1, 20) } returns
            Pair(Fixtures.notifications(2), 2)
        notificationApp(service)

        val response = client.get("/api/v1/notifications") { header("X-User-ID", "user-a") }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = response.jsonBody()
        assertEquals(2, body.getValue("data").jsonArray.size)
        assertEquals(2, body.getValue("total").jsonPrimitive.int)
        assertEquals(1, body.getValue("page").jsonPrimitive.int)
        assertEquals(20, body.getValue("pageSize").jsonPrimitive.int)
    }

    @Test
    fun `list accepts user_id as a query parameter fallback`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 1, 20) } returns Pair(emptyList(), 0)
        notificationApp(service)

        val response = client.get("/api/v1/notifications?user_id=user-a")

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(0, response.jsonBody().getValue("total").jsonPrimitive.int)
    }

    @Test
    fun `list returns an empty page for a user with no notifications`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 1, 20) } returns Pair(emptyList(), 0)
        notificationApp(service)

        val body = client.get("/api/v1/notifications") { header("X-User-ID", "user-a") }.jsonBody()

        assertEquals(0, body.getValue("data").jsonArray.size)
        assertEquals(false, body.getValue("hasMore").jsonPrimitive.boolean)
    }

    @Test
    fun `hasMore is true when the requested page ends one item short of the total`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 2, 10) } returns Pair(Fixtures.notifications(10), 21)
        notificationApp(service)

        val body = client.get("/api/v1/notifications?page=2&page_size=10") {
            header("X-User-ID", "user-a")
        }.jsonBody()

        assertTrue(body.getValue("hasMore").jsonPrimitive.boolean)
    }

    @Test
    fun `hasMore is false when the requested page ends exactly on the total`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 2, 10) } returns Pair(Fixtures.notifications(10), 20)
        notificationApp(service)

        val body = client.get("/api/v1/notifications?page=2&page_size=10") {
            header("X-User-ID", "user-a")
        }.jsonBody()

        assertEquals(false, body.getValue("hasMore").jsonPrimitive.boolean)
    }

    @Test
    fun `hasMore is false when the requested page runs past the total`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 2, 10) } returns Pair(Fixtures.notifications(9), 19)
        notificationApp(service)

        val body = client.get("/api/v1/notifications?page=2&page_size=10") {
            header("X-User-ID", "user-a")
        }.jsonBody()

        assertEquals(false, body.getValue("hasMore").jsonPrimitive.boolean)
    }

    @Test
    fun `non-numeric paging parameters fall back to the defaults`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 1, 20) } returns Pair(emptyList(), 0)
        notificationApp(service)

        val response = client.get("/api/v1/notifications?page=abc&page_size=xyz") {
            header("X-User-ID", "user-a")
        }

        assertEquals(HttpStatusCode.OK, response.status)
        coVerify(exactly = 1) { service.getNotifications("user-a", 1, 20) }
    }

    @Test
    fun `the camelCase pageSize parameter used by the web client is ignored`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 1, 20) } returns Pair(emptyList(), 0)
        notificationApp(service)

        client.get("/api/v1/notifications?page=1&pageSize=5") { header("X-User-ID", "user-a") }

        // The route only reads `page_size`; the client's `pageSize` never takes effect.
        coVerify(exactly = 1) { service.getNotifications("user-a", 1, 20) }
    }

    @Test
    fun `a page index of zero is forwarded to the service unvalidated`() = testApplication {
        val service = service()
        coEvery { service.getNotifications("user-a", 0, 20) } returns Pair(emptyList(), 0)
        notificationApp(service)

        val response = client.get("/api/v1/notifications?page=0") { header("X-User-ID", "user-a") }

        assertEquals(HttpStatusCode.OK, response.status)
        coVerify(exactly = 1) { service.getNotifications("user-a", 0, 20) }
    }

    // ── the live 400 regression (docs/exploratory-qa-report.md) ───────────────

    @Test
    fun `list returns 400 today when no user id reaches the service`() = testApplication {
        notificationApp(service())

        val response = client.get("/api/v1/notifications?page=1&pageSize=20")

        assertEquals(HttpStatusCode.BadRequest, response.status)
        assertTrue(response.jsonBody().getValue("error").jsonPrimitive.content.contains("user_id"))
    }

    @Test
    fun `unread-count returns 400 today when no user id reaches the service`() = testApplication {
        notificationApp(service())

        val response = client.get("/api/v1/notifications/unread-count")

        assertEquals(HttpStatusCode.BadRequest, response.status)
    }

    @Test
    fun `a blank X-User-ID header is rejected`() = testApplication {
        notificationApp(service())

        val response = client.get("/api/v1/notifications") { header("X-User-ID", "   ") }

        assertEquals(HttpStatusCode.BadRequest, response.status)
    }

    @Ignore(
        "DEFECT (live, see docs/exploratory-qa-report.md 'Notifications API returns 400 " +
            "everywhere'): an authenticated caller gets 400 from GET /api/v1/notifications. The " +
            "route only accepts identity via the X-User-ID header or a user_id query parameter and " +
            "ignores the bearer token the browser actually sends, so any request whose X-User-ID is " +
            "not injected upstream fails. A fix should resolve the caller from the JWT and return " +
            "200 with a payload. Not fixed here: this package is test-only."
    )
    @Test
    fun `list returns 200 for a bearer-authenticated caller without an X-User-ID header`() = testApplication {
        val service = service()
        coEvery { service.getNotifications(any(), any(), any()) } returns Pair(Fixtures.notifications(1), 1)
        notificationApp(service)

        val response = client.get("/api/v1/notifications?page=1&pageSize=20") {
            header("Authorization", "Bearer test-token-for-user-a")
        }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(1, response.jsonBody().getValue("data").jsonArray.size)
    }

    @Ignore(
        "DEFECT (live, see docs/exploratory-qa-report.md): GET /api/v1/notifications/unread-count " +
            "returns 400 on every authenticated page because the route resolves the caller only " +
            "from X-User-ID / user_id and ignores the bearer token. A fix should return 200 with " +
            "the unread count. Not fixed here: this package is test-only."
    )
    @Test
    fun `unread-count returns 200 for a bearer-authenticated caller without an X-User-ID header`() =
        testApplication {
            val service = service()
            coEvery { service.getUnreadCount(any()) } returns 4
            notificationApp(service)

            val response = client.get("/api/v1/notifications/unread-count") {
                header("Authorization", "Bearer test-token-for-user-a")
            }

            assertEquals(HttpStatusCode.OK, response.status)
            assertEquals(4, response.jsonBody().getValue("unreadCount").jsonPrimitive.int)
        }

    // ── unread-count boundaries ───────────────────────────────────────────────

    @Test
    fun `unread-count is zero for a user with no notifications`() = testApplication {
        val service = service()
        coEvery { service.getUnreadCount("user-a") } returns 0
        notificationApp(service)

        val body = client.get("/api/v1/notifications/unread-count") {
            header("X-User-ID", "user-a")
        }.jsonBody()

        assertEquals(0, body.getValue("unreadCount").jsonPrimitive.int)
        assertEquals("user-a", body.getValue("userId").jsonPrimitive.content)
    }

    @Test
    fun `unread-count is one for a user with a single unread notification`() = testApplication {
        val service = service()
        coEvery { service.getUnreadCount("user-a") } returns 1
        notificationApp(service)

        val body = client.get("/api/v1/notifications/unread-count") {
            header("X-User-ID", "user-a")
        }.jsonBody()

        assertEquals(1, body.getValue("unreadCount").jsonPrimitive.int)
    }

    @Test
    fun `unread-count reports N for a user with several unread notifications`() = testApplication {
        val service = service()
        coEvery { service.getUnreadCount("user-a") } returns 7
        notificationApp(service)

        val body = client.get("/api/v1/notifications/unread-count") {
            header("X-User-ID", "user-a")
        }.jsonBody()

        assertEquals(7, body.getValue("unreadCount").jsonPrimitive.int)
    }

    @Test
    fun `unread-count drops to zero after read-all marks everything read`() = testApplication {
        val service = service()
        coEvery { service.getUnreadCount("user-a") } returnsMany listOf(3, 0)
        coEvery { service.markAllAsRead("user-a") } returns 3
        notificationApp(service)

        val before = client.get("/api/v1/notifications/unread-count") { header("X-User-ID", "user-a") }
        val readAll = client.put("/api/v1/notifications/read-all") { header("X-User-ID", "user-a") }
        val after = client.get("/api/v1/notifications/unread-count") { header("X-User-ID", "user-a") }

        assertEquals(3, before.jsonBody().getValue("unreadCount").jsonPrimitive.int)
        assertEquals(3, readAll.jsonBody().getValue("markedCount").jsonPrimitive.int)
        assertEquals(0, after.jsonBody().getValue("unreadCount").jsonPrimitive.int)
    }

    @Test
    fun `read-all requires a user id`() = testApplication {
        notificationApp(service())

        assertEquals(HttpStatusCode.BadRequest, client.put("/api/v1/notifications/read-all").status)
    }

    @Test
    fun `read-all reports zero when there is nothing to mark`() = testApplication {
        val service = service()
        coEvery { service.markAllAsRead("user-a") } returns 0
        notificationApp(service)

        val body = client.put("/api/v1/notifications/read-all") {
            header("X-User-ID", "user-a")
        }.jsonBody()

        assertEquals(0, body.getValue("markedCount").jsonPrimitive.int)
    }

    // ── single notification lifecycle ─────────────────────────────────────────

    @Test
    fun `fetching a notification that does not exist returns 404`() = testApplication {
        val service = service()
        coEvery { service.getNotificationById("missing") } returns null
        notificationApp(service)

        assertEquals(
            HttpStatusCode.NotFound,
            client.get("/api/v1/notifications/missing") { header("X-User-ID", "user-a") }.status,
        )
    }

    @Test
    fun `marking an unknown notification read returns 404`() = testApplication {
        val service = service()
        coEvery { service.markAsRead("missing") } returns false
        notificationApp(service)

        assertEquals(
            HttpStatusCode.NotFound,
            client.put("/api/v1/notifications/missing/read") { header("X-User-ID", "user-a") }.status,
        )
    }

    @Test
    fun `deleting an unknown notification returns 404`() = testApplication {
        val service = service()
        coEvery { service.deleteNotification("missing") } returns false
        notificationApp(service)

        assertEquals(
            HttpStatusCode.NotFound,
            client.delete("/api/v1/notifications/missing") { header("X-User-ID", "user-a") }.status,
        )
    }

    @Test
    fun `the owner can mark their own notification read`() = testApplication {
        val service = service()
        coEvery { service.markAsRead("n-a") } returns true
        notificationApp(service)

        assertEquals(
            HttpStatusCode.NoContent,
            client.put("/api/v1/notifications/n-a/read") { header("X-User-ID", "user-a") }.status,
        )
    }

    @Test
    fun `the web client's PATCH read verb is not routed`() = testApplication {
        notificationApp(service())

        // frontend/client-app/src/lib/api.ts issues PATCH /notifications/{id}/read and
        // POST /notifications/read-all; the service only exposes PUT for both.
        assertEquals(
            HttpStatusCode.NotFound,
            client.patch("/api/v1/notifications/n-a/read") { header("X-User-ID", "user-a") }.status,
        )
    }

    @Test
    fun `the web client's POST read-all verb is not routed`() = testApplication {
        notificationApp(service())

        assertEquals(
            HttpStatusCode.MethodNotAllowed,
            client.post("/api/v1/notifications/read-all") { header("X-User-ID", "user-a") }.status,
        )
    }

    // ── cross-user authorization ──────────────────────────────────────────────

    @Test
    fun `a user can currently read another user's notification by id`() = testApplication {
        val service = service()
        coEvery { service.getNotificationById("n-b") } returns
            Fixtures.notification(id = "n-b", userId = "user-b")
        notificationApp(service)

        val response = client.get("/api/v1/notifications/n-b") { header("X-User-ID", "user-a") }

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals("user-b", response.jsonBody().getValue("userId").jsonPrimitive.content)
    }

    @Ignore(
        "DEFECT: no ownership check on GET /api/v1/notifications/{id}. The handler looks the " +
            "notification up by id alone and never compares its userId with the calling X-User-ID, " +
            "so any user can read any other user's notification (IDOR). A fix should return 404 " +
            "for a notification the caller does not own. Not fixed here: this package is test-only."
    )
    @Test
    fun `a user must not read another user's notification by id`() = testApplication {
        val service = service()
        coEvery { service.getNotificationById("n-b") } returns
            Fixtures.notification(id = "n-b", userId = "user-b")
        notificationApp(service)

        val response = client.get("/api/v1/notifications/n-b") { header("X-User-ID", "user-a") }

        assertEquals(HttpStatusCode.NotFound, response.status)
    }

    @Test
    fun `a user can currently mark another user's notification read`() = testApplication {
        val service = service()
        coEvery { service.getNotificationById("n-b") } returns
            Fixtures.notification(id = "n-b", userId = "user-b")
        coEvery { service.markAsRead("n-b") } returns true
        notificationApp(service)

        val response = client.put("/api/v1/notifications/n-b/read") { header("X-User-ID", "user-a") }

        assertEquals(HttpStatusCode.NoContent, response.status)
        coVerify(exactly = 1) { service.markAsRead("n-b") }
    }

    @Ignore(
        "DEFECT: no ownership check on PUT /api/v1/notifications/{id}/read. The handler marks any " +
            "notification read by id without comparing its owner to the calling X-User-ID, so a " +
            "user can mutate another user's notifications (IDOR). A fix should reject the call and " +
            "leave the notification untouched. Not fixed here: this package is test-only."
    )
    @Test
    fun `a user must not mark another user's notification read`() = testApplication {
        val service = service()
        coEvery { service.getNotificationById("n-b") } returns
            Fixtures.notification(id = "n-b", userId = "user-b")
        coEvery { service.markAsRead("n-b") } returns true
        notificationApp(service)

        val response = client.put("/api/v1/notifications/n-b/read") { header("X-User-ID", "user-a") }

        assertEquals(HttpStatusCode.NotFound, response.status)
        coVerify(exactly = 0) { service.markAsRead("n-b") }
    }

    @Test
    fun `a user can currently delete another user's notification`() = testApplication {
        val service = service()
        coEvery { service.deleteNotification("n-b") } returns true
        notificationApp(service)

        val response = client.delete("/api/v1/notifications/n-b") { header("X-User-ID", "user-a") }

        assertEquals(HttpStatusCode.NoContent, response.status)
    }

    @Ignore(
        "DEFECT: no ownership check on DELETE /api/v1/notifications/{id}; any user can delete any " +
            "notification by id (IDOR). A fix should reject the call. Not fixed here: this package " +
            "is test-only."
    )
    @Test
    fun `a user must not delete another user's notification`() = testApplication {
        val service = service()
        coEvery { service.deleteNotification("n-b") } returns true
        notificationApp(service)

        val response = client.delete("/api/v1/notifications/n-b") { header("X-User-ID", "user-a") }

        assertEquals(HttpStatusCode.NotFound, response.status)
        coVerify(exactly = 0) { service.deleteNotification("n-b") }
    }

    // ── preferences ───────────────────────────────────────────────────────────

    @Test
    fun `preferences require a user id`() = testApplication {
        notificationApp(service())

        assertEquals(HttpStatusCode.BadRequest, client.get("/api/v1/preferences").status)
    }

    @Test
    fun `preferences are returned for the identified user`() = testApplication {
        val service = service()
        coEvery { service.getPreferences("user-a") } returns NotificationPreference(userId = "user-a")
        notificationApp(service)

        val body = client.get("/api/v1/preferences") { header("X-User-ID", "user-a") }.jsonBody()

        assertEquals("user-a", body.getValue("userId").jsonPrimitive.content)
        assertTrue((body.getValue("channels") as JsonObject).containsKey("file_shared"))
    }

    @Test
    fun `updating preferences stores the requested channels`() = testApplication {
        val service = service()
        val channels = slot<List<DeliveryChannel>>()
        coEvery { service.updatePreferences("user-a", "file_shared", capture(channels)) } returns Unit
        notificationApp(service)

        val response = client.put("/api/v1/preferences") {
            header("X-User-ID", "user-a")
            contentType(ContentType.Application.Json)
            setBody("""{"userId":"user-a","eventType":"file_shared","channels":["IN_APP"]}""")
        }

        assertEquals(HttpStatusCode.NoContent, response.status)
        assertEquals(listOf(DeliveryChannel.IN_APP), channels.captured)
    }

    @Test
    fun `updating preferences with an unparseable body fails the request`() = testApplication {
        val service = service()
        notificationApp(service)

        val response = client.put("/api/v1/preferences") {
            header("X-User-ID", "user-a")
            contentType(ContentType.Application.Json)
            setBody("""{"userId":"user-a"}""")
        }

        assertEquals(HttpStatusCode.InternalServerError, response.status)
        coVerify(exactly = 0) { service.updatePreferences(any(), any(), any()) }
    }

    @Test
    fun `updating preferences with an unknown channel name fails the request`() = testApplication {
        val service = service()
        notificationApp(service)

        val response = client.put("/api/v1/preferences") {
            header("X-User-ID", "user-a")
            contentType(ContentType.Application.Json)
            setBody("""{"userId":"user-a","eventType":"file_shared","channels":["CARRIER_PIGEON"]}""")
        }

        assertEquals(HttpStatusCode.InternalServerError, response.status)
        coVerify(exactly = 0) { service.updatePreferences(any(), any(), any()) }
    }

    @Test
    fun `a user can currently overwrite another user's preferences`() = testApplication {
        val service = service()
        notificationApp(service)

        val response = client.put("/api/v1/preferences") {
            header("X-User-ID", "user-a")
            contentType(ContentType.Application.Json)
            setBody("""{"userId":"user-b","eventType":"file_shared","channels":[]}""")
        }

        assertEquals(HttpStatusCode.NoContent, response.status)
        coVerify(exactly = 1) { service.updatePreferences("user-b", "file_shared", emptyList()) }
    }

    @Ignore(
        "DEFECT: PUT /api/v1/preferences takes the target user from the request body and never " +
            "compares it with the calling X-User-ID, so a user can silently rewrite (including " +
            "fully mute) another user's notification preferences. A fix should reject a body whose " +
            "userId is not the caller. Not fixed here: this package is test-only."
    )
    @Test
    fun `a user must not overwrite another user's preferences`() = testApplication {
        val service = service()
        notificationApp(service)

        val response = client.put("/api/v1/preferences") {
            header("X-User-ID", "user-a")
            contentType(ContentType.Application.Json)
            setBody("""{"userId":"user-b","eventType":"file_shared","channels":[]}""")
        }

        assertEquals(HttpStatusCode.Forbidden, response.status)
        coVerify(exactly = 0) { service.updatePreferences(any(), any(), any()) }
    }

    // ── operational endpoints ─────────────────────────────────────────────────

    @Test
    fun `health reports the service name`() = testApplication {
        notificationApp(service())

        val body = client.get("/health").jsonBody()

        assertEquals("healthy", body.getValue("status").jsonPrimitive.content)
        assertEquals("notification-service", body.getValue("service").jsonPrimitive.content)
    }

    @Test
    fun `metrics are exposed in prometheus text format`() = testApplication {
        notificationApp(service())

        val response = client.get("/metrics")

        assertEquals(HttpStatusCode.OK, response.status)
        assertEquals(ContentType.Text.Plain, response.contentType()?.withoutParameters())
    }

    @Test
    fun `an unknown route returns 404`() = testApplication {
        notificationApp(service())

        assertEquals(HttpStatusCode.NotFound, client.get("/api/v1/nope").status)
    }
}
