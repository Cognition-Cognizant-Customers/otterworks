package com.otterworks.notification

import aws.sdk.kotlin.services.dynamodb.DynamoDbClient
import aws.sdk.kotlin.services.ses.SesClient
import aws.sdk.kotlin.services.sqs.SqsClient
import aws.smithy.kotlin.runtime.net.url.Url
import com.otterworks.notification.config.AppConfig
import com.otterworks.notification.consumer.SqsConsumer
import com.otterworks.notification.plugins.configureMonitoring
import io.micrometer.core.instrument.MeterRegistry
import com.otterworks.notification.repository.NotificationRepository
import com.otterworks.notification.routes.configureRouting
import com.otterworks.notification.service.EmailSender
import com.otterworks.notification.service.NotificationService
import com.otterworks.notification.websocket.WebSocketManager
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.serialization.kotlinx.json.json
import io.ktor.server.application.Application
import io.ktor.server.application.install
import io.ktor.server.engine.embeddedServer
import io.ktor.server.netty.Netty
import io.ktor.server.plugins.contentnegotiation.ContentNegotiation
import io.ktor.server.plugins.cors.routing.CORS
import io.ktor.server.plugins.statuspages.StatusPages
import io.ktor.server.response.respond
import io.ktor.server.websocket.WebSockets
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import io.github.oshai.kotlinlogging.KotlinLogging
import org.koin.core.module.dsl.singleOf
import org.koin.dsl.module
import org.koin.ktor.ext.inject
import org.koin.ktor.plugin.Koin
import org.koin.logger.slf4jLogger
import java.time.Duration

private val logger = KotlinLogging.logger {}

fun main() {
    val config = AppConfig.load()

    embeddedServer(Netty, port = config.port, host = "0.0.0.0") {
        module(config)
    }.start(wait = true)
}

fun Application.module(config: AppConfig = AppConfig.load()) {
    val prometheusRegistry = configureMonitoring()
    configurePlugins(config)
    configureDependencyInjection(config, prometheusRegistry)
    configureRouting(prometheusRegistry)

    val sqsConsumer by inject<SqsConsumer>()
    launch {
        sqsConsumer.startPolling()
    }

    logger.info { "Notification Service started on port ${config.port}" }
}

fun Application.configurePlugins(config: AppConfig = AppConfig.load()) {
    install(ContentNegotiation) {
        json(Json {
            prettyPrint = false
            isLenient = true
            ignoreUnknownKeys = true
            encodeDefaults = true
        })
    }

    install(WebSockets) {
        pingPeriodMillis = 30000
        timeoutMillis = 15000
        maxFrameSize = Long.MAX_VALUE
        masking = false
    }

    install(CORS) {
        allowHost("localhost:3000")
        allowHost("localhost:4200")
        allowHeader(HttpHeaders.ContentType)
        allowHeader(HttpHeaders.Authorization)
        allowMethod(HttpMethod.Put)
        allowMethod(HttpMethod.Delete)
        allowMethod(HttpMethod.Patch)
    }

    install(StatusPages) {
        exception<Throwable> { call, cause ->
            logger.error(cause) { "Unhandled exception" }
            call.respond(
                status = HttpStatusCode.InternalServerError,
                message = mapOf("error" to "Internal server error")
            )
        }
    }
}

fun Application.configureDependencyInjection(
    config: AppConfig,
    prometheusRegistry: io.micrometer.prometheusmetrics.PrometheusMeterRegistry,
) {
    install(Koin) {
        slf4jLogger()
        modules(
            module {
                single { config }
                single { prometheusRegistry as io.micrometer.core.instrument.MeterRegistry }

                single<SqsClient> {
                    SqsClient {
                        region = config.awsRegion
                        config.awsEndpointUrl?.let { endpointUrl = Url.parse(it) }
                    }
                }

                single<DynamoDbClient> {
                    DynamoDbClient {
                        region = config.awsRegion
                        config.awsEndpointUrl?.let { endpointUrl = Url.parse(it) }
                    }
                }

                single<SesClient> {
                    SesClient {
                        region = config.awsRegion
                        config.awsEndpointUrl?.let { endpointUrl = Url.parse(it) }
                    }
                }

                singleOf(::WebSocketManager)

                single { NotificationRepository(get<DynamoDbClient>(), get<AppConfig>()) }
                single { EmailSender(get<SesClient>(), get<AppConfig>()) }
                single {
                    NotificationService(
                        repository = get<NotificationRepository>(),
                        emailSender = get<EmailSender>(),
                        webSocketManager = get<WebSocketManager>(),
                        meterRegistry = get<io.micrometer.core.instrument.MeterRegistry>(),
                    )
                }
                single { SqsConsumer(get<SqsClient>(), get<NotificationService>(), get<AppConfig>(), get<MeterRegistry>()) }
            }
        )
    }
}
