package com.otterworks.analytics.api

import akka.http.scaladsl.model.{ContentTypes, StatusCodes}
import akka.http.scaladsl.server.MethodRejection
import akka.http.scaladsl.testkit.ScalatestRouteTest
import com.otterworks.analytics.config.PostgresConfig
import com.otterworks.analytics.model.*
import com.otterworks.analytics.repository.{InMemoryMetricsRepository, MetricsRepository}
import com.otterworks.analytics.service.AnalyticsService
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import scala.concurrent.{ExecutionContext, Future}

/**
 * Health and metrics endpoints (WP-12).
 *
 * `HealthRoutes` had no direct coverage: the happy path was only exercised
 * transitively, and the degraded branch -- the one that matters to a load
 * balancer -- not at all. The failing repository below is a stub rather than a
 * real outage, so nothing here waits on a clock or a socket.
 */
class HealthRoutesSpec extends AnyFlatSpec with Matchers with ScalatestRouteTest:

  private val testConfig: PostgresConfig = PostgresConfig(
    url = "jdbc:postgresql://localhost:5432/test",
    user = "test",
    password = "test",
    maxPoolSize = 2
  )

  /** A repository whose every read fails, standing in for a database outage. */
  private class UnavailableRepository(using ec: ExecutionContext) extends MetricsRepository:
    private def down[A]: Future[A] = Future.failed(new RuntimeException("connection refused"))
    def storeEvent(event: AnalyticsEvent): Future[Unit] = down
    def getDashboardSummary(period: String): Future[DashboardSummary] = down
    def getUserActivity(userId: String): Future[UserActivity] = down
    def getDocumentStats(documentId: String): Future[DocumentStats] = down
    def getTopContent(contentType: String, period: String, limit: Int): Future[TopContentResponse] = down
    def getActiveUsers(period: String): Future[ActiveUsersResponse] = down
    def getStorageUsage(userId: Option[String]): Future[StorageUsageResponse] = down
    def getExportData(period: String): Future[List[Map[String, String]]] = down
    def getEventCount: Future[Long] = down

  private def healthRoutes(repository: MetricsRepository): HealthRoutes =
    HealthRoutes(AnalyticsService(repository))

  private def freshRepository(): MetricsRepository = new InMemoryMetricsRepository(testConfig)

  private def event(userId: String): AnalyticsEvent =
    AnalyticsEvent.create("document.viewed", userId, "doc-1", "document", Map.empty)

  "GET /health" should "report healthy with a zero event count on an empty store" in {
    Get("/health") ~> healthRoutes(freshRepository()).routes ~> check {
      status shouldBe StatusCodes.OK
      contentType shouldBe ContentTypes.`application/json`
      responseAs[String] shouldBe
        """{"status":"healthy","service":"analytics-service","eventsProcessed":0}"""
    }
  }

  it should "report the running event count" in {
    val repository = freshRepository()
    val service = AnalyticsService(repository)
    val routes = HealthRoutes(service).routes

    Get("/health") ~> routes ~> check {
      responseAs[String] should include("\"eventsProcessed\":0")
    }

    // Three writes, three events: the count is a total, not a distinct count.
    List("user-1", "user-2", "user-1").foreach { user =>
      scala.concurrent.Await.result(repository.storeEvent(event(user)), scala.concurrent.duration.Duration.Inf)
    }

    Get("/health") ~> routes ~> check {
      status shouldBe StatusCodes.OK
      responseAs[String] should include("\"eventsProcessed\":3")
    }
  }

  it should "degrade to 503 when the store is unreachable" in {
    Get("/health") ~> healthRoutes(new UnavailableRepository).routes ~> check {
      status shouldBe StatusCodes.ServiceUnavailable
      contentType shouldBe ContentTypes.`application/json`
      responseAs[String] shouldBe
        """{"status":"degraded","service":"analytics-service","eventsProcessed":0}"""
    }
  }

  it should "reject a non-GET method rather than answering it" in {
    Post("/health") ~> healthRoutes(freshRepository()).routes ~> check {
      handled shouldBe false
      rejections should contain(MethodRejection(akka.http.scaladsl.model.HttpMethods.GET))
    }
  }

  it should "not match a path that merely starts with health" in {
    Get("/healthz") ~> healthRoutes(freshRepository()).routes ~> check {
      handled shouldBe false
    }
  }

  "GET /metrics" should "serve the Prometheus text exposition format" in {
    Get("/metrics") ~> healthRoutes(freshRepository()).routes ~> check {
      status shouldBe StatusCodes.OK
      contentType shouldBe ContentTypes.`text/plain(UTF-8)`
      // An empty default registry is legitimate output; the contract is that the
      // endpoint answers 200 with a text body rather than failing.
      responseAs[String] should not be null
    }
  }

  it should "stay available while the store is down" in {
    // /metrics must not depend on the database, or a scrape goes dark exactly
    // when the metrics are most useful.
    Get("/metrics") ~> healthRoutes(new UnavailableRepository).routes ~> check {
      status shouldBe StatusCodes.OK
    }
  }

  it should "reject a non-GET method" in {
    Post("/metrics") ~> healthRoutes(freshRepository()).routes ~> check {
      handled shouldBe false
    }
  }
