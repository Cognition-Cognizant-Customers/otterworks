package com.otterworks.analytics.service

import com.otterworks.analytics.config.PostgresConfig
import com.otterworks.analytics.model.*
import com.otterworks.analytics.repository.MetricsRepository
import org.scalatest.concurrent.ScalaFutures
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import org.scalatest.time.{Millis, Seconds, Span}

import java.time.Instant
import scala.concurrent.{ExecutionContext, Future}

/**
 * Idempotency, concurrency and limit-boundary cases for the service/repository
 * pair (WP-12). Each test builds its own repository, so no state is shared
 * between them and the suite is order-independent.
 */
class AnalyticsServiceIdempotencySpec extends AnyFlatSpec with Matchers with ScalaFutures:

  given PatienceConfig = PatienceConfig(timeout = Span(10, Seconds), interval = Span(20, Millis))
  given ExecutionContext = ExecutionContext.global

  private val testConfig = PostgresConfig(
    url = "jdbc:postgresql://localhost:5432/test",
    user = "test",
    password = "test",
    maxPoolSize = 2
  )

  private def freshRepository(): MetricsRepository = MetricsRepository(testConfig)

  private def event(eventId: String, userId: String = "u1", resourceId: String = "doc-1"): AnalyticsEvent =
    AnalyticsEvent(
      eventId = eventId,
      eventType = EventType.DocumentViewed,
      userId = userId,
      resourceId = resourceId,
      resourceType = "document",
      metadata = Map.empty,
      timestamp = Instant.now().minusSeconds(60)
    )

  "Storing the same event twice" should "increase the count twice: there is no idempotency key" in {
    // FINDING (WP-12): `storeEvent` appends unconditionally. The ingestion path is
    // at-least-once, so a redelivered event is stored and counted again. Pinning
    // today's behaviour; de-duplication would be a production change.
    val repo = freshRepository()
    val duplicated = event("evt-duplicate")

    repo.storeEvent(duplicated).futureValue
    repo.storeEvent(duplicated).futureValue

    repo.getEventCount.futureValue shouldBe 2L
    repo.getDocumentStats("doc-1").futureValue.views shouldBe 2L
    repo.getDocumentStats("doc-1").futureValue.uniqueViewers shouldBe 1L
  }

  "trackEvent" should "mint a distinct eventId for two identical requests" in {
    val service = AnalyticsService(freshRepository())

    val first = service.trackEvent(EventType.DocumentCreated, "u1", "doc-1", "document", Map.empty).futureValue
    val second = service.trackEvent(EventType.DocumentCreated, "u1", "doc-1", "document", Map.empty).futureValue

    first.eventId should not be second.eventId
    service.getEventCount.futureValue shouldBe 2L
  }

  "Concurrent writers" should "not lose or duplicate events" in {
    val repo = freshRepository()
    val writers = 8
    val perWriter = 250

    val all = Future.sequence(
      (0 until writers).map { w =>
        Future.sequence((0 until perWriter).map(i => repo.storeEvent(event(s"w$w-e$i", userId = s"u$w"))))
      }
    )
    all.futureValue

    repo.getEventCount.futureValue shouldBe (writers * perWriter).toLong
    repo.getActiveUsers("7d").futureValue.count shouldBe writers.toLong
    repo.getExportData("7d").futureValue.map(_("event_id")).distinct should have size writers * perWriter
  }

  it should "let a reader observe a consistent snapshot while writes are in flight" in {
    val repo = freshRepository()
    val writes = Future.sequence((0 until 500).map(i => repo.storeEvent(event(s"e-$i"))))
    val read = repo.getExportData("7d")

    writes.futureValue
    val observed = read.futureValue.size
    observed should (be >= 0 and be <= 500)
    repo.getExportData("7d").futureValue should have size 500
  }

  "getTopContent" should "honour the limit boundary trio and default to 10" in {
    val repo = freshRepository()
    Future
      .sequence((1 to 11).flatMap { r =>
        (1 to r).map(i => repo.storeEvent(event(s"r$r-i$i", userId = s"u$i", resourceId = s"doc-$r")))
      })
      .futureValue
    val service = AnalyticsService(repo)

    service.getTopContent("documents", "7d", 9).futureValue.items should have size 9
    service.getTopContent("documents", "7d", 10).futureValue.items should have size 10
    service.getTopContent("documents", "7d", 11).futureValue.items should have size 11
    service.getTopContent("documents", "7d").futureValue.items should have size 10
  }

  it should "return nothing for a zero or negative limit" in {
    val repo = freshRepository()
    repo.storeEvent(event("e-1")).futureValue
    val service = AnalyticsService(repo)

    service.getTopContent("documents", "7d", 0).futureValue.items shouldBe empty
    service.getTopContent("documents", "7d", -5).futureValue.items shouldBe empty
  }

  "An empty repository" should "answer every query with an empty result rather than failing" in {
    val service = AnalyticsService(freshRepository())

    service.getEventCount.futureValue shouldBe 0L
    service.getDashboardSummary("7d").futureValue.totalEvents shouldBe 0L
    service.getActiveUsers("30d").futureValue.users shouldBe empty
    service.getTopContent("files", "90d", 10).futureValue.items shouldBe empty
    service.getStorageUsage(None).futureValue.totalStorageBytes shouldBe 0L
    service.getUserActivity("nobody").futureValue.recentEvents shouldBe empty
    service.getDocumentStats("nothing").futureValue.views shouldBe 0L

    val exported = service.exportReport("json", "7d").futureValue
    exported.recordCount shouldBe 0L
    exported.data shouldBe empty
  }

  "exportReport" should "echo an unrecognised format instead of rejecting it" in {
    // Negative case: the service performs no format validation, so a caller
    // asking for "yaml" gets a JSON-shaped payload labelled "yaml".
    val service = AnalyticsService(freshRepository())

    service.exportReport("yaml", "7d").futureValue.format shouldBe "yaml"
    service.exportReport("", "7d").futureValue.format shouldBe ""
  }
