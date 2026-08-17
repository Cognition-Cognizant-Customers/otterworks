package com.otterworks.notification.routes

import com.otterworks.notification.model.NotificationPreferenceRequest
import com.otterworks.notification.model.PaginatedResponse
import com.otterworks.notification.model.UnreadCountResponse
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.websocket.WebSocketManager
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.Application
import io.ktor.server.application.call
import io.ktor.server.request.receive
import io.ktor.server.response.respond
import io.ktor.server.response.respondText
import io.ktor.server.routing.delete
import io.ktor.server.routing.get
import io.ktor.server.routing.put
import io.ktor.server.routing.route
import io.ktor.server.routing.routing
import io.ktor.server.websocket.webSocket
import io.ktor.websocket.CloseReason
import io.ktor.websocket.Frame
import io.ktor.websocket.close
import io.ktor.websocket.readText
import io.micrometer.prometheus.PrometheusMeterRegistry
import kotlinx.serialization.Serializable
import org.koin.ktor.ext.inject

private const val USER_ID_HEADER = "X-User-ID"
private const val USER_ID_REQUIRED_MESSAGE = "user_id is required (via X-User-ID header or query parameter)"
private const val NOTIFICATION_ID_REQUIRED_MESSAGE = "Notification ID is required"
private const val NOTIFICATION_NOT_FOUND_MESSAGE = "Notification not found"

@Serializable
data class HealthResponse(val status: String, val service: String)

@Serializable
data class ErrorResponse(val error: String)

@Serializable
data class MarkAllReadResponse(val markedCount: Int)

fun Application.configureRouting(prometheusRegistry: PrometheusMeterRegistry) {
    val notificationService by inject<NotificationService>()
    val webSocketManager by inject<WebSocketManager>()

    routing {
        get("/health") {
            call.respond(HealthResponse(status = "healthy", service = "notification-service"))
        }

        get("/metrics") {
            call.respondText(
                prometheusRegistry.scrape(),
                contentType = io.ktor.http.ContentType.Text.Plain,
            )
        }

        route("/api/v1/notifications") {
            get {
                val userId = call.request.headers[USER_ID_HEADER] ?: call.request.queryParameters["user_id"]
                if (userId.isNullOrBlank()) {
                    call.respond(HttpStatusCode.BadRequest, ErrorResponse(USER_ID_REQUIRED_MESSAGE))
                    return@get
                }

                val page = call.request.queryParameters["page"]?.toIntOrNull() ?: 1
                val pageSize = call.request.queryParameters["page_size"]?.toIntOrNull() ?: 20

                val (notifications, total) = notificationService.getNotifications(userId, page, pageSize)

                call.respond(
                    PaginatedResponse(
                        data = notifications,
                        total = total,
                        page = page,
                        pageSize = pageSize,
                        hasMore = (page * pageSize) < total,
                    )
                )
            }

            get("/unread-count") {
                val userId = call.request.headers[USER_ID_HEADER] ?: call.request.queryParameters["user_id"]
                if (userId.isNullOrBlank()) {
                    call.respond(HttpStatusCode.BadRequest, ErrorResponse(USER_ID_REQUIRED_MESSAGE))
                    return@get
                }

                val count = notificationService.getUnreadCount(userId)
                call.respond(UnreadCountResponse(userId = userId, unreadCount = count))
            }

            get("/{id}") {
                val id = call.parameters["id"] ?: return@get call.respond(
                    HttpStatusCode.BadRequest,
                    ErrorResponse(NOTIFICATION_ID_REQUIRED_MESSAGE),
                )

                val notification = notificationService.getNotificationById(id)
                if (notification != null) {
                    call.respond(notification)
                } else {
                    call.respond(HttpStatusCode.NotFound, ErrorResponse(NOTIFICATION_NOT_FOUND_MESSAGE))
                }
            }

            put("/{id}/read") {
                val id = call.parameters["id"] ?: return@put call.respond(
                    HttpStatusCode.BadRequest,
                    ErrorResponse(NOTIFICATION_ID_REQUIRED_MESSAGE),
                )

                val success = notificationService.markAsRead(id)
                if (success) {
                    call.respond(HttpStatusCode.NoContent)
                } else {
                    call.respond(HttpStatusCode.NotFound, ErrorResponse(NOTIFICATION_NOT_FOUND_MESSAGE))
                }
            }

            put("/read-all") {
                val userId = call.request.headers[USER_ID_HEADER] ?: call.request.queryParameters["user_id"]
                if (userId.isNullOrBlank()) {
                    call.respond(HttpStatusCode.BadRequest, ErrorResponse(USER_ID_REQUIRED_MESSAGE))
                    return@put
                }

                val count = notificationService.markAllAsRead(userId)
                call.respond(MarkAllReadResponse(markedCount = count))
            }

            delete("/{id}") {
                val id = call.parameters["id"] ?: return@delete call.respond(
                    HttpStatusCode.BadRequest,
                    ErrorResponse(NOTIFICATION_ID_REQUIRED_MESSAGE),
                )

                val success = notificationService.deleteNotification(id)
                if (success) {
                    call.respond(HttpStatusCode.NoContent)
                } else {
                    call.respond(HttpStatusCode.NotFound, ErrorResponse(NOTIFICATION_NOT_FOUND_MESSAGE))
                }
            }
        }

        route("/api/v1/preferences") {
            get {
                val userId = call.request.headers[USER_ID_HEADER] ?: call.request.queryParameters["user_id"]
                if (userId.isNullOrBlank()) {
                    call.respond(HttpStatusCode.BadRequest, ErrorResponse(USER_ID_REQUIRED_MESSAGE))
                    return@get
                }

                val preferences = notificationService.getPreferences(userId)
                call.respond(preferences)
            }

            put {
                val request = call.receive<NotificationPreferenceRequest>()
                notificationService.updatePreferences(
                    userId = request.userId,
                    eventType = request.eventType,
                    channels = request.channels,
                )
                call.respond(HttpStatusCode.NoContent)
            }
        }

        webSocket("/ws/notifications/{userId}") {
            val userId = call.parameters["userId"]
            if (userId.isNullOrBlank()) {
                close(CloseReason(CloseReason.Codes.VIOLATED_POLICY, "userId is required"))
                return@webSocket
            }

            webSocketManager.addConnection(userId, this)

            try {
                for (frame in incoming) {
                    when (frame) {
                        is Frame.Text -> {
                            val text = frame.readText()
                            // Handle ping/pong or client messages if needed
                            if (text == "ping") {
                                send(Frame.Text("pong"))
                            }
                        }
                        is Frame.Close -> break
                        else -> { /* ignore other frame types */ }
                    }
                }
            } finally {
                webSocketManager.removeConnection(userId, this)
            }
        }
    }
}
