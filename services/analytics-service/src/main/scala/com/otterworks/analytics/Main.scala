package com.otterworks.analytics

import akka.actor.typed.ActorSystem
import akka.actor.typed.scaladsl.Behaviors
import akka.http.scaladsl.Http
import akka.http.scaladsl.server.Directives.*
import akka.http.scaladsl.server.Route
import com.otterworks.analytics.api.{AnalyticsRoutes, EventRoutes, HealthRoutes, MarginRoutes, MarketIngestRoutes}
import com.otterworks.analytics.batch.MarketSeeder
import com.otterworks.analytics.config.AppConfig
import com.otterworks.analytics.db.AnalyticsDb
import com.otterworks.analytics.repository.{InMemoryMetricsRepository, MarketRepository, MetricsRepository, PostgresMetricsRepository}
import com.otterworks.analytics.service.{AnalyticsService, EventProcessor, MarginService}

import scala.concurrent.{Await, ExecutionContextExecutor}
import scala.concurrent.duration.Duration
import scala.util.{Failure, Success}

object Main:
  def main(args: Array[String]): Unit =
    given system: ActorSystem[Nothing] = ActorSystem(Behaviors.empty, "analytics-service")
    given ec: ExecutionContextExecutor = system.executionContext

    val config = AppConfig.load()

    // Wire up the metrics store. The golden default is the durable PostgreSQL
    // store (the "before" state for the S3/Iceberg lakehouse migration); the
    // in-memory store remains available via config for local runs and tests.
    // If the durable store cannot be initialised (e.g. DB unreachable), fall
    // back to in-memory so the service still boots — mirroring the non-fatal
    // SQS handling below.
    val (repository: MetricsRepository, marketDb: Option[AnalyticsDb]) =
      if config.repository.isPostgres then
        val db = new AnalyticsDb(config.postgres)
        try
          db.migrate()
          sys.addShutdownHook(db.close())
          system.log.info("Analytics using durable PostgreSQL metrics store")
          (new PostgresMetricsRepository(db), Some(db))
        catch
          case ex: Throwable =>
            db.close()
            system.log.warn(
              s"Durable PostgreSQL store unavailable (${ex.getMessage}); falling back to in-memory store")
            (new InMemoryMetricsRepository(config.postgres), None)
      else
        system.log.info("Analytics using in-memory metrics store (per configuration)")
        (new InMemoryMetricsRepository(config.postgres), None)

    val analyticsService = AnalyticsService(repository)
    val eventProcessor = EventProcessor(config, analyticsService)

    // Market/margin feature (requires the durable Postgres store): seed the
    // deterministic synthetic baseline, then serve the margins/market routes.
    // Without Postgres the endpoints answer 503 rather than vanishing.
    val marketRoutes: Route = marketDb match
      case Some(db) =>
        val marketRepository = new MarketRepository(db)
        val marginService = new MarginService(marketRepository)
        try MarketSeeder.run(marketRepository, marginService)
        catch
          case ex: Exception =>
            system.log.warn(s"Market baseline seed failed: ${ex.getMessage}")
        concat(
          MarginRoutes(marginService, marketRepository).routes,
          MarketIngestRoutes(marginService, marketRepository).routes,
        )
      case None =>
        pathPrefix("api" / "v1" / "analytics" / ("margins" | "market")) {
          complete(
            akka.http.scaladsl.model.StatusCodes.ServiceUnavailable,
            "margins/market endpoints require the durable PostgreSQL store")
        }

    // Build routes
    val healthRoutes = HealthRoutes(analyticsService)
    val analyticsRoutes = AnalyticsRoutes(analyticsService)
    val eventRoutes = EventRoutes(analyticsService)

    val routes: Route = concat(
      healthRoutes.routes,
      eventRoutes.routes,
      analyticsRoutes.routes,
      marketRoutes,
    )

    val host = config.server.host
    val port = config.server.port

    val binding = Http().newServerAt(host, port).bind(routes)
    binding.onComplete {
      case Success(b) =>
        system.log.info(s"Analytics Service started at http://${b.localAddress.getHostString}:${b.localAddress.getPort}")
        // Start SQS consumer in background (non-fatal if SQS unavailable)
        try eventProcessor.start()
        catch case ex: Exception =>
          system.log.warn(s"SQS event processor could not start: ${ex.getMessage}. Running without SQS ingestion.")
      case Failure(e) =>
        system.log.error(s"Failed to start Analytics Service: ${e.getMessage}")
        system.terminate()
    }

    Await.result(system.whenTerminated, Duration.Inf)
