package com.otterworks.notification.routes

import aws.sdk.kotlin.services.dynamodb.DynamoDbClient
import aws.sdk.kotlin.services.dynamodb.model.AttributeValue
import aws.sdk.kotlin.services.dynamodb.model.QueryRequest
import aws.sdk.kotlin.services.dynamodb.model.QueryResponse
import aws.sdk.kotlin.services.ses.SesClient
import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.configurePlugins
import com.otterworks.notification.repository.NotificationRepository
import com.otterworks.notification.service.EmailSender
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.websocket.WebSocketManager
import io.ktor.client.request.get
import io.ktor.client.request.header
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
import kotlin.test.assertTrue

/**
 * Pagination behaviour of `GET /api/v1/notifications` with the real service and
 * repository behind it (only the DynamoDB client is faked), so the effect of an
 * unvalidated `page` / `page_size` is visible end to end.
 */
class RoutesPaginationEndToEndTest {

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

    private fun item(id: String) = mapOf(
        "id" to AttributeValue.S(id),
        "userId" to AttributeValue.S(USER_ID),
        "type" to AttributeValue.S("file_shared"),
        "title" to AttributeValue.S("File Shared With You"),
        "message" to AttributeValue.S("A file was shared with you"),
        "read" to AttributeValue.Bool(false),
        "createdAt" to AttributeValue.S("2024-01-01T00:00:00Z"),
    )

    private fun ApplicationTestBuilder.withStoredNotifications(count: Int) {
        val dynamoDbClient = mockk<DynamoDbClient>()
        coEvery { dynamoDbClient.query(any<QueryRequest>()) } returns QueryResponse {
            items = (1..count).map { item("n-$it") }
            this.count = count
            lastEvaluatedKey = null
        }

        val repository = NotificationRepository(dynamoDbClient, config)
        val service = NotificationService(
            repository = repository,
            emailSender = EmailSender(mockk<SesClient>(relaxed = true), config),
            webSocketManager = WebSocketManager(),
            meterRegistry = null,
        )

        application {
            configurePlugins(config)
            install(KoinIsolated) {
                modules(module { single { service }; single { WebSocketManager() } })
            }
            configureRouting(PrometheusMeterRegistry(PrometheusConfig.DEFAULT))
        }
    }

    @Test
    fun `GET notifications with a valid page returns that slice`() = testApplication {
        withStoredNotifications(25)

        val response = client.get("/api/v1/notifications?page=2&page_size=10") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = Json.parseToJsonElement(response.bodyAsText()).jsonObject
        assertEquals(10, body["data"]!!.jsonArray.size)
        assertEquals(25, body["total"]!!.jsonPrimitive.int)
        assertTrue(body["hasMore"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `GET notifications with page_size zero returns no data but claims more pages`() = testApplication {
        withStoredNotifications(25)

        val response = client.get("/api/v1/notifications?page_size=0") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = Json.parseToJsonElement(response.bodyAsText()).jsonObject
        assertEquals(0, body["data"]!!.jsonArray.size)
        assertEquals(25, body["total"]!!.jsonPrimitive.int)
        assertTrue(body["hasMore"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `GET notifications with a page_size of one million returns every stored notification`() = testApplication {
        withStoredNotifications(25)

        val response = client.get("/api/v1/notifications?page_size=1000000") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.OK, response.status)
        val body = Json.parseToJsonElement(response.bodyAsText()).jsonObject
        assertEquals(25, body["data"]!!.jsonArray.size)
        assertEquals(1_000_000, body["pageSize"]!!.jsonPrimitive.int)
    }

    @Test
    fun `GET notifications with a negative page_size returns 500`() = testApplication {
        withStoredNotifications(25)

        val response = client.get("/api/v1/notifications?page_size=-1") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.InternalServerError, response.status)
    }

    @Test
    fun `GET notifications with page zero returns 500`() = testApplication {
        withStoredNotifications(25)

        val response = client.get("/api/v1/notifications?page=0") { header(USER_HEADER, USER_ID) }

        assertEquals(HttpStatusCode.InternalServerError, response.status)
    }

    companion object {
        private const val USER_ID = "user-1"
        private const val USER_HEADER = "X-User-ID"
    }
}
