package com.otterworks.analytics.event

import com.otterworks.analytics.model.{AnalyticsEvent, DailyUsageRollup}
import software.amazon.awssdk.services.dynamodb.DynamoDbClient
import software.amazon.awssdk.services.dynamodb.model.{
  AttributeValue,
  GetItemRequest,
  Put,
  TransactWriteItem,
  TransactWriteItemsRequest,
  TransactionCanceledException,
  Update
}

import java.time.Instant
import scala.jdk.CollectionConverters.*

/**
 * Persistence for per-day incremental rollup state. Each event is applied
 * individually and idempotently: implementations must record the `eventId` and
 * fold the event's delta into the stored state atomically, so redelivered
 * events (SQS is at-least-once) are never counted twice and concurrent
 * invocations touching the same date never lose updates.
 */
trait RollupStore:
  /** Read the finished rollup projection for a date (derived values included). */
  def get(date: String): Option[DailyUsageRollup]

  /**
   * Atomically apply one event's delta to the state for its date, recording
   * the eventId. Returns false (a no-op) if the event was already applied.
   */
  def applyEvent(event: AnalyticsEvent): Boolean

/** In-memory store used by tests and the local comparison harness. */
final class InMemoryRollupStore extends RollupStore:
  private var states: Map[String, DailyRollupState] = Map.empty
  private var processed: Set[String] = Set.empty

  def get(date: String): Option[DailyUsageRollup] = states.get(date).map(_.toRollup)

  def applyEvent(event: AnalyticsEvent): Boolean = synchronized {
    if processed.contains(event.eventId) then false
    else
      processed = processed + event.eventId
      val date = IncrementalUsageRollup.dateOf(event.timestamp)
      val state = states.getOrElse(date, DailyRollupState.empty(date))
      states = states.updated(date, state(event))
      true
  }

  def snapshot: Map[String, DailyRollupState] = states

object DynamoDbRollupStore:
  /** How long processed eventIds are retained for deduplication. Must exceed
    * the DLQ retention window (14 d) so a late redrive cannot double-count. */
  val DedupeTtl: java.time.Duration = java.time.Duration.ofDays(30)

  /**
   * How long first-seen (date, userId) markers are retained. Longer than the
   * dedupe window so a late event for an already-counted user does not
   * increment `activeUsers` twice.
   */
  val UserMarkerTtl: java.time.Duration = java.time.Duration.ofDays(35)

  /** Bounded retries for same-date transaction conflicts under concurrency. */
  val MaxConflictRetries: Int = 5

/**
 * DynamoDB-backed store: one rollup item per calendar date (keyed on `date`)
 * plus a marker ledger (keyed on `eventId`, TTL-expired) holding both
 * processed-event markers and first-seen `user#<date>#<userId>` markers. Each
 * event is applied with a single `TransactWriteItems`: conditional puts of the
 * eventId marker and (for first-seen users) the user marker, and an
 * `UpdateItem` `ADD` on the numeric counters including an exact `activeUsers`
 * counter that is incremented only when the user marker was newly written.
 * The rollup item therefore stays small and bounded regardless of daily user
 * cardinality (no user-id set on the item), while `activeUsers` remains an
 * exact distinct count. The conditions make redelivered events no-ops and the
 * `ADD` semantics make concurrent same-date updates commutative, so neither
 * duplicates nor races corrupt the rollup. `get` returns the finished
 * [[DailyUsageRollup]] projection with `netStorageBytes` derived on read.
 */
final class DynamoDbRollupStore(client: DynamoDbClient, tableName: String, dedupeTableName: String)
    extends RollupStore:

  def get(date: String): Option[DailyUsageRollup] =
    val request = GetItemRequest
      .builder()
      .tableName(tableName)
      .key(Map("date" -> AttributeValue.fromS(date)).asJava)
      .consistentRead(true)
      .build()
    val item = client.getItem(request).item()
    if item == null || item.isEmpty then None
    else
      val allocated = n(item, "storageAllocatedBytes")
      val released = n(item, "storageReleasedBytes")
      Some(
        DailyUsageRollup(
          date = item.get("date").s(),
          totalEvents = n(item, "totalEvents"),
          activeUsers = n(item, "activeUsers"),
          documentsCreated = n(item, "documentsCreated"),
          documentsViewed = n(item, "documentsViewed"),
          documentsEdited = n(item, "documentsEdited"),
          filesUploaded = n(item, "filesUploaded"),
          filesDownloaded = n(item, "filesDownloaded"),
          collabSessions = n(item, "collabSessions"),
          storageAllocatedBytes = allocated,
          storageReleasedBytes = released,
          netStorageBytes = allocated - released
        )
      )

  def applyEvent(event: AnalyticsEvent): Boolean =
    val date = IncrementalUsageRollup.dateOf(event.timestamp)
    val delta = DailyRollupState.empty(date)(event)
    val eventMarker = condPut(event.eventId, DynamoDbRollupStore.DedupeTtl)
    val userMarker = condPut(s"user#$date#${event.userId}", DynamoDbRollupStore.UserMarkerTtl)
    // Transaction item order matters: cancellation reasons come back in the
    // same order, letting us tell a duplicate event (index 0) from an
    // already-counted user (index 1). All events for one calendar date update
    // the same rollup item, so concurrent invocations can also collide with
    // TransactionConflict, which the SDK does not retry; retry with jittered
    // backoff before failing the record.
    var attempt = 0
    var firstSeenUser = true
    while true do
      val items =
        if firstSeenUser then List(eventMarker, userMarker, updateFor(delta, newUsers = 1L))
        else List(eventMarker, updateFor(delta, newUsers = 0L))
      val request = TransactWriteItemsRequest.builder().transactItems(items.asJava).build()
      try
        client.transactWriteItems(request)
        return true
      catch
        case ex: TransactionCanceledException =>
          val reasons = ex.cancellationReasons().asScala.map(_.code()).toList
          if reasons.headOption.contains("ConditionalCheckFailed") then return false // duplicate event
          else if firstSeenUser && reasons.lift(1).contains("ConditionalCheckFailed") then
            firstSeenUser = false // user already counted for this date
          else if attempt < DynamoDbRollupStore.MaxConflictRetries &&
            reasons.exists(c => c == "TransactionConflict" || c == "TransactionInProgress")
          then
            attempt += 1
            Thread.sleep(scala.util.Random.between(10L, 50L) * attempt)
          else throw ex
    false

  private def condPut(markerId: String, ttl: java.time.Duration): TransactWriteItem =
    val expiresAt = Instant.now().plus(ttl).getEpochSecond
    TransactWriteItem
      .builder()
      .put(
        Put
          .builder()
          .tableName(dedupeTableName)
          .item(
            Map(
              "eventId" -> AttributeValue.fromS(markerId),
              "expiresAt" -> AttributeValue.fromN(expiresAt.toString)
            ).asJava
          )
          .conditionExpression("attribute_not_exists(eventId)")
          .build()
      )
      .build()

  private def updateFor(delta: DailyRollupState, newUsers: Long): TransactWriteItem =
    val counters = List(
      "totalEvents" -> delta.totalEvents,
      "activeUsers" -> newUsers,
      "documentsCreated" -> delta.documentsCreated,
      "documentsViewed" -> delta.documentsViewed,
      "documentsEdited" -> delta.documentsEdited,
      "filesUploaded" -> delta.filesUploaded,
      "filesDownloaded" -> delta.filesDownloaded,
      "collabSessions" -> delta.collabSessions,
      "storageAllocatedBytes" -> delta.storageAllocatedBytes,
      "storageReleasedBytes" -> delta.storageReleasedBytes
    )
    val values = scala.collection.mutable.Map[String, AttributeValue]()
    val adds = scala.collection.mutable.ListBuffer[String]()
    counters.foreach { case (name, value) =>
      adds += s"#$name :$name"
      values(s":$name") = AttributeValue.fromN(value.toString)
    }
    val names = counters.map { case (name, _) => s"#$name" -> name }.toMap
    TransactWriteItem
      .builder()
      .update(
        Update
          .builder()
          .tableName(tableName)
          .key(Map("date" -> AttributeValue.fromS(delta.date)).asJava)
          .updateExpression("ADD " + adds.mkString(", "))
          .expressionAttributeNames(names.asJava)
          .expressionAttributeValues(values.toMap.asJava)
          .build()
      )
      .build()

  private def n(item: java.util.Map[String, AttributeValue], key: String): Long =
    Option(item.get(key)).map(_.n().toLong).getOrElse(0L)
