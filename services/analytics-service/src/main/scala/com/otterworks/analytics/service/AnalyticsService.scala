package com.otterworks.analytics.service

import com.otterworks.analytics.event.{NoopUsageEventPublisher, UsageEventPublisher}
import com.otterworks.analytics.model.*
import com.otterworks.analytics.repository.MetricsRepository
import org.slf4j.LoggerFactory

import java.time.Instant
import scala.concurrent.{ExecutionContext, Future}

/**
 * Core analytics business logic. Coordinates event tracking with the
 * repository layer and provides query methods for all analytics endpoints.
 */
class AnalyticsService(
    repository: MetricsRepository,
    publisher: UsageEventPublisher = NoopUsageEventPublisher
)(using ec: ExecutionContext):

  private val logger = LoggerFactory.getLogger(getClass)

  /** Track a new analytics event and persist it. */
  def trackEvent(
      eventType: String,
      userId: String,
      resourceId: String,
      resourceType: String,
      metadata: Map[String, String]
  ): Future[AnalyticsEvent] =
    val event = AnalyticsEvent.create(eventType, userId, resourceId, resourceType, metadata)
    logger.info("Tracking event: type={}, user={}, resource={}", event.eventType, event.userId, event.resourceId)
    repository.storeEvent(event).map { _ =>
      publisher.publish(event)
      event
    }

  /** Get aggregated dashboard metrics for the given period. */
  def getDashboardSummary(period: String): Future[DashboardSummary] =
    logger.debug("Fetching dashboard summary for period={}", period)
    repository.getDashboardSummary(period)

  /** Get activity for a specific user. */
  def getUserActivity(userId: String): Future[UserActivity] =
    logger.debug("Fetching activity for user={}", userId)
    repository.getUserActivity(userId)

  /** Get analytics for a specific document. */
  def getDocumentStats(documentId: String): Future[DocumentStats] =
    logger.debug("Fetching stats for document={}", documentId)
    repository.getDocumentStats(documentId)

  /** Get top content ranked by activity. */
  def getTopContent(contentType: String, period: String, limit: Int = 10): Future[TopContentResponse] =
    logger.debug("Fetching top content: type={}, period={}, limit={}", contentType, period, limit.toString)
    repository.getTopContent(contentType, period, limit)

  /** Get active users for the given period. */
  def getActiveUsers(period: String): Future[ActiveUsersResponse] =
    logger.debug("Fetching active users for period={}", period)
    repository.getActiveUsers(period)

  /** Get storage usage, optionally filtered by user. */
  def getStorageUsage(userId: Option[String]): Future[StorageUsageResponse] =
    logger.debug("Fetching storage usage for userId={}", userId.toString)
    repository.getStorageUsage(userId)

  /** Export analytics data for the given period. */
  def exportReport(format: String, period: String): Future[ExportReportResponse] =
    logger.info("Exporting analytics report: format={}, period={}", format, period)
    repository.getExportData(period).map { data =>
      ExportReportResponse(
        format = format,
        period = period,
        generatedAt = Instant.now().toString,
        recordCount = data.size.toLong,
        data = data
      )
    }

  /** Get the total event count (used for health/metrics). */
  def getEventCount: Future[Long] =
    repository.getEventCount
