package com.otterworks.notification.routes

import com.otterworks.notification.model.NotificationPreferenceRequest
import com.otterworks.notification.model.PaginatedResponse
import com.otterworks.notification.model.UnreadCountResponse
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.websocket.WebSocketManager
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.Application
import io.ktor.server.application.ApplicationCall
import io.ktor.server.application.call
import io.ktor.server.request.receive
import io.ktor.server.response.respond
import io.ktor.server.response.respondText
import io.ktor.server.routing.Route
import io.ktor.server.routing.delete
import io.ktor.server.routing.get
import io.ktor.server.routing.put
import io.ktor.server.routing.route
import io.ktor.server.routing.routing
import io.ktor.server.websocket.DefaultWebSocketServerSession
import io.ktor.server.websocket.webSocket
import io.ktor.websocket.CloseReason
import io.ktor.websocket.Frame
import io.ktor.websocket.close
import io.ktor.websocket.readText
import io.micrometer.prometheus.PrometheusMeterRegistry
import kotlinx.serialization.Serializable
import org.koin.ktor.ext.inject

@Serializable
data class HealthResponse(val status: String, val service: String)

@Serializable
data class ErrorResponse(val error: String)

@Serializable
data class MarkAllReadResponse(val markedCount: Int)

private const val USER_ID_REQUIRED = "user_id is required (via X-User-ID header or query parameter)"
private const val NOTIFICATION_ID_REQUIRED = "Notification ID is required"
private const val NOTIFICATION_NOT_FOUND = "Notification not found"

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

        route("/api/v1/notifications") { notificationRoutes(notificationService) }
        route("/api/v1/preferences") { preferenceRoutes(notificationService) }
        notificationWebSocket(webSocketManager)
    }
}

private fun Route.notificationRoutes(notificationService: NotificationService) {
    get {
        val userId = call.requireUserId() ?: return@get

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
        val userId = call.requireUserId() ?: return@get
        val count = notificationService.getUnreadCount(userId)
        call.respond(UnreadCountResponse(userId = userId, unreadCount = count))
    }

    get("/{id}") {
        val id = call.requireNotificationId() ?: return@get
        val notification = notificationService.getNotificationById(id)
        if (notification != null) {
            call.respond(notification)
        } else {
            call.respond(HttpStatusCode.NotFound, ErrorResponse(NOTIFICATION_NOT_FOUND))
        }
    }

    put("/{id}/read") {
        val id = call.requireNotificationId() ?: return@put
        call.respondNotificationOutcome(notificationService.markAsRead(id))
    }

    put("/read-all") {
        val userId = call.requireUserId() ?: return@put
        val count = notificationService.markAllAsRead(userId)
        call.respond(MarkAllReadResponse(markedCount = count))
    }

    delete("/{id}") {
        val id = call.requireNotificationId() ?: return@delete
        call.respondNotificationOutcome(notificationService.deleteNotification(id))
    }
}

private fun Route.preferenceRoutes(notificationService: NotificationService) {
    get {
        val userId = call.requireUserId() ?: return@get
        call.respond(notificationService.getPreferences(userId))
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

private fun Route.notificationWebSocket(webSocketManager: WebSocketManager) {
    webSocket("/ws/notifications/{userId}") {
        val userId = call.parameters["userId"]
        if (userId.isNullOrBlank()) {
            close(CloseReason(CloseReason.Codes.VIOLATED_POLICY, "userId is required"))
            return@webSocket
        }

        webSocketManager.addConnection(userId, this)
        try {
            consumeClientFrames()
        } finally {
            webSocketManager.removeConnection(userId, this)
        }
    }
}

private suspend fun DefaultWebSocketServerSession.consumeClientFrames() {
    for (frame in incoming) {
        when (frame) {
            // Handle ping/pong or client messages if needed
            is Frame.Text -> if (frame.readText() == "ping") send(Frame.Text("pong"))
            is Frame.Close -> break
            else -> { /* ignore other frame types */ }
        }
    }
}

private suspend fun ApplicationCall.requireUserId(): String? {
    val userId = request.headers["X-User-ID"] ?: request.queryParameters["user_id"]
    if (userId.isNullOrBlank()) {
        respond(HttpStatusCode.BadRequest, ErrorResponse(USER_ID_REQUIRED))
        return null
    }
    return userId
}

private suspend fun ApplicationCall.requireNotificationId(): String? {
    val id = parameters["id"]
    if (id == null) {
        respond(HttpStatusCode.BadRequest, ErrorResponse(NOTIFICATION_ID_REQUIRED))
    }
    return id
}

private suspend fun ApplicationCall.respondNotificationOutcome(success: Boolean) {
    if (success) {
        respond(HttpStatusCode.NoContent)
    } else {
        respond(HttpStatusCode.NotFound, ErrorResponse(NOTIFICATION_NOT_FOUND))
    }
}
