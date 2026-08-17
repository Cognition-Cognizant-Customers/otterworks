package com.otterworks.analytics.service

import com.otterworks.analytics.config.PostgresConfig
import com.otterworks.analytics.model.*
import com.otterworks.analytics.repository.MetricsRepository
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import org.scalatest.concurrent.ScalaFutures
import org.scalatest.time.{Millis, Seconds, Span}

import scala.concurrent.ExecutionContext

class AnalyticsServiceSpec extends AnyFlatSpec with Matchers with ScalaFutures:

  given PatienceConfig = PatienceConfig(timeout = Span(5, Seconds), interval = Span(100, Millis))
  given ExecutionContext = ExecutionContext.global

  private val UserId = "user-1"
  private val UserTwoId = "user-2"
  private val FileId = "file-1"

  private val testConfig = PostgresConfig(
    url = "jdbc:postgresql://localhost:5432/test",
    user = "test",
    password = "test",
    maxPoolSize = 2
  )

  private def createService(): AnalyticsService =
    val repo = MetricsRepository(testConfig)
    AnalyticsService(repo)

  "AnalyticsService.trackEvent" should "create and store an event" in {
    val service = createService()

    val result = service.trackEvent(
      EventType.DocumentCreated,
      UserId,
      "doc-1",
      "document",
      Map("title" -> "Test Document")
    ).futureValue

    result.eventType shouldBe EventType.DocumentCreated
    result.userId shouldBe UserId
    result.resourceId shouldBe "doc-1"
    result.eventId should not be empty
  }

  "AnalyticsService.getDashboardSummary" should "return aggregated metrics" in {
    val service = createService()

    // Track some events
    service.trackEvent(EventType.DocumentCreated, UserId, "doc-1", "document", Map.empty).futureValue
    service.trackEvent(EventType.DocumentCreated, UserTwoId, "doc-2", "document", Map.empty).futureValue
    service.trackEvent(EventType.FileUploaded, UserId, FileId, "file", Map.empty).futureValue
    service.trackEvent(EventType.CollabSessionStarted, UserId, "session-1", "session", Map.empty).futureValue

    val summary = service.getDashboardSummary("7d").futureValue

    summary.period shouldBe "7d"
    summary.dailyActiveUsers shouldBe 2
    summary.documentsCreated shouldBe 2
    summary.filesUploaded shouldBe 1
    summary.collabSessions shouldBe 1
    summary.totalEvents shouldBe 4
  }

  "AnalyticsService.getUserActivity" should "return activity for a specific user" in {
    val service = createService()

    service.trackEvent(EventType.DocumentCreated, UserId, "doc-1", "document", Map.empty).futureValue
    service.trackEvent(EventType.DocumentViewed, UserId, "doc-1", "document", Map.empty).futureValue
    service.trackEvent(EventType.FileUploaded, UserId, FileId, "file", Map.empty).futureValue
    service.trackEvent(EventType.DocumentCreated, UserTwoId, "doc-2", "document", Map.empty).futureValue

    val activity = service.getUserActivity(UserId).futureValue

    activity.userId shouldBe UserId
    activity.totalEvents shouldBe 3
    activity.documentsCreated shouldBe 1
    activity.documentsViewed shouldBe 1
    activity.filesUploaded shouldBe 1
    activity.recentEvents.size shouldBe 3
  }

  "AnalyticsService.getDocumentStats" should "return document-level analytics" in {
    val service = createService()

    service.trackEvent(EventType.DocumentViewed, UserId, "doc-1", "document", Map.empty).futureValue
    service.trackEvent(EventType.DocumentViewed, UserTwoId, "doc-1", "document", Map.empty).futureValue
    service.trackEvent(EventType.DocumentEdited, UserId, "doc-1", "document", Map.empty).futureValue
    service.trackEvent(EventType.DocumentShared, UserId, "doc-1", "document", Map.empty).futureValue

    val stats = service.getDocumentStats("doc-1").futureValue

    stats.documentId shouldBe "doc-1"
    stats.views shouldBe 2
    stats.edits shouldBe 1
    stats.shares shouldBe 1
    stats.uniqueViewers shouldBe 2
  }

  "AnalyticsService.getTopContent" should "return content ranked by activity" in {
    val service = createService()

    // doc-1 gets 3 events, doc-2 gets 1
    service.trackEvent(EventType.DocumentViewed, UserId, "doc-1", "document", Map("title" -> "Popular")).futureValue
    service.trackEvent(EventType.DocumentViewed, UserTwoId, "doc-1", "document", Map("title" -> "Popular")).futureValue
    service.trackEvent(EventType.DocumentEdited, UserId, "doc-1", "document", Map("title" -> "Popular")).futureValue
    service.trackEvent(EventType.DocumentViewed, UserId, "doc-2", "document", Map("title" -> "Less Popular")).futureValue

    val response = service.getTopContent("documents", "7d", 10).futureValue

    response.items.size shouldBe 2
    response.items.head.resourceId shouldBe "doc-1"
    response.items.head.eventCount shouldBe 3
    response.items(1).resourceId shouldBe "doc-2"
  }

  "AnalyticsService.getActiveUsers" should "return users ranked by activity" in {
    val service = createService()

    service.trackEvent(EventType.DocumentViewed, UserId, "doc-1", "document", Map.empty).futureValue
    service.trackEvent(EventType.DocumentViewed, UserId, "doc-2", "document", Map.empty).futureValue
    service.trackEvent(EventType.DocumentViewed, UserTwoId, "doc-1", "document", Map.empty).futureValue

    val response = service.getActiveUsers("daily").futureValue

    response.count shouldBe 2
    response.users.head.userId shouldBe UserId
    response.users.head.eventCount shouldBe 2
  }

  "AnalyticsService.getStorageUsage" should "calculate storage metrics" in {
    val service = createService()

    service.trackEvent(EventType.StorageAllocated, UserId, FileId, "file", Map("bytes" -> "1024")).futureValue
    service.trackEvent(EventType.StorageAllocated, UserId, "file-2", "file", Map("bytes" -> "2048")).futureValue
    service.trackEvent(EventType.FileUploaded, UserId, FileId, "file", Map.empty).futureValue

    val usage = service.getStorageUsage(Some(UserId)).futureValue

    usage.userId shouldBe Some(UserId)
    usage.totalStorageBytes shouldBe 3072
    usage.filesCount shouldBe 1
  }

  "AnalyticsService.exportReport" should "return event data for the period" in {
    val service = createService()

    service.trackEvent(EventType.DocumentCreated, UserId, "doc-1", "document", Map.empty).futureValue
    service.trackEvent(EventType.FileUploaded, UserId, FileId, "file", Map.empty).futureValue

    val report = service.exportReport("json", "7d").futureValue

    report.format shouldBe "json"
    report.period shouldBe "7d"
    report.recordCount shouldBe 2
    report.data.size shouldBe 2
    report.data.head.keys should contain allOf("event_id", "event_type", "user_id")
  }
