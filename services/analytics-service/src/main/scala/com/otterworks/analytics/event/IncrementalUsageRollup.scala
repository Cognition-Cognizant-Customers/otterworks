package com.otterworks.analytics.event

import com.otterworks.analytics.model.*

import java.time.{Instant, LocalDate, ZoneOffset}

/**
 * Mutable-free per-day rollup state for the event-driven usage-rollup path.
 *
 * Where the batch [[com.otterworks.analytics.batch.UsageRollupAggregator]]
 * aggregates a bulk collection in one pass, this state is upserted one event at
 * a time as events arrive from EventBridge -> SQS -> Lambda. Distinct active
 * users are tracked as an explicit user-id set so `activeUsers` stays exact
 * under incremental updates. Folding the same events through this state yields
 * byte-for-byte the same [[DailyUsageRollup]] as the batch aggregator,
 * regardless of arrival order.
 */
final case class DailyRollupState(
    date: String, // ISO-8601 calendar date (yyyy-MM-dd), UTC
    totalEvents: Long,
    userIds: Set[String],
    documentsCreated: Long,
    documentsViewed: Long,
    documentsEdited: Long,
    filesUploaded: Long,
    filesDownloaded: Long,
    collabSessions: Long,
    storageAllocatedBytes: Long,
    storageReleasedBytes: Long,
    // Read-model only: exact distinct-user count maintained externally (e.g. as
    // a DynamoDB counter with one first-seen marker per (date, userId)), used
    // instead of userIds.size when the full id set is not materialised.
    activeUsersOverride: Option[Long] = None
):

  /** Apply one event to this day's state (the incremental upsert step). */
  def apply(event: AnalyticsEvent): DailyRollupState =
    copy(
      totalEvents = totalEvents + 1,
      userIds = userIds + event.userId,
      documentsCreated = documentsCreated + inc(event, EventType.DocumentCreated),
      documentsViewed = documentsViewed + inc(event, EventType.DocumentViewed),
      documentsEdited = documentsEdited + inc(event, EventType.DocumentEdited),
      filesUploaded = filesUploaded + inc(event, EventType.FileUploaded),
      filesDownloaded = filesDownloaded + inc(event, EventType.FileDownloaded),
      collabSessions = collabSessions + inc(event, EventType.CollabSessionStarted),
      storageAllocatedBytes = storageAllocatedBytes + bytesIf(event, EventType.StorageAllocated),
      storageReleasedBytes = storageReleasedBytes + bytesIf(event, EventType.StorageReleased)
    )

  /** Merge two states for the same date (counter sums, user-id set union). */
  def combine(other: DailyRollupState): DailyRollupState =
    require(date == other.date, s"cannot combine states for $date and ${other.date}")
    DailyRollupState(
      date = date,
      totalEvents = totalEvents + other.totalEvents,
      userIds = userIds ++ other.userIds,
      documentsCreated = documentsCreated + other.documentsCreated,
      documentsViewed = documentsViewed + other.documentsViewed,
      documentsEdited = documentsEdited + other.documentsEdited,
      filesUploaded = filesUploaded + other.filesUploaded,
      filesDownloaded = filesDownloaded + other.filesDownloaded,
      collabSessions = collabSessions + other.collabSessions,
      storageAllocatedBytes = storageAllocatedBytes + other.storageAllocatedBytes,
      storageReleasedBytes = storageReleasedBytes + other.storageReleasedBytes
    )

  /** Project the state into the same output shape the batch job produces. */
  def toRollup: DailyUsageRollup =
    DailyUsageRollup(
      date = date,
      totalEvents = totalEvents,
      activeUsers = activeUsersOverride.getOrElse(userIds.size.toLong),
      documentsCreated = documentsCreated,
      documentsViewed = documentsViewed,
      documentsEdited = documentsEdited,
      filesUploaded = filesUploaded,
      filesDownloaded = filesDownloaded,
      collabSessions = collabSessions,
      storageAllocatedBytes = storageAllocatedBytes,
      storageReleasedBytes = storageReleasedBytes,
      netStorageBytes = storageAllocatedBytes - storageReleasedBytes
    )

  private def inc(event: AnalyticsEvent, eventType: String): Long =
    if event.eventType == eventType then 1L else 0L

  private def bytesIf(event: AnalyticsEvent, eventType: String): Long =
    if event.eventType == eventType then
      scala.util.Try(event.metadata.getOrElse("bytes", "0").toLong).getOrElse(0L)
    else 0L

object DailyRollupState:
  def empty(date: String): DailyRollupState =
    DailyRollupState(date, 0L, Set.empty, 0L, 0L, 0L, 0L, 0L, 0L, 0L, 0L)

/**
 * Pure incremental rollup logic shared by the Lambda handler and tests.
 * Semantics mirror [[com.otterworks.analytics.batch.UsageRollupAggregator]].
 */
object IncrementalUsageRollup:

  /** UTC calendar date an event belongs to. */
  def dateOf(timestamp: Instant): String =
    timestamp.atZone(ZoneOffset.UTC).toLocalDate.toString

  /** Upsert one event into the per-day state map. */
  def upsert(states: Map[String, DailyRollupState], event: AnalyticsEvent): Map[String, DailyRollupState] =
    val date = dateOf(event.timestamp)
    val state = states.getOrElse(date, DailyRollupState.empty(date))
    states.updated(date, state(event))

  /** Fold a stream of events through the incremental upsert. */
  def applyAll(states: Map[String, DailyRollupState], events: Seq[AnalyticsEvent]): Map[String, DailyRollupState] =
    events.foldLeft(states)(upsert)

  /** Project all states into rollups, ordered ascending by date. */
  def rollups(states: Map[String, DailyRollupState]): List[DailyUsageRollup] =
    states.values.toList.sortBy(_.date).map(_.toRollup)
