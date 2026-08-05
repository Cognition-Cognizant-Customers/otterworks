package com.otterworks.analytics.event

import com.otterworks.analytics.model.AnalyticsEvent
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
  def get(date: String): Option[DailyRollupState]

  /**
   * Atomically apply one event's delta to the state for its date, recording
   * the eventId. Returns false (a no-op) if the event was already applied.
   */
  def applyEvent(event: AnalyticsEvent): Boolean

/** In-memory store used by tests and the local comparison harness. */
final class InMemoryRollupStore extends RollupStore:
  private var states: Map[String, DailyRollupState] = Map.empty
  private var processed: Set[String] = Set.empty

  def get(date: String): Option[DailyRollupState] = states.get(date)

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
  /** How long processed eventIds are retained for deduplication. */
  val DedupeTtl: java.time.Duration = java.time.Duration.ofDays(14)

/**
 * DynamoDB-backed store: one rollup item per calendar date (keyed on `date`)
 * plus a processed-event ledger (keyed on `eventId`, TTL-expired). Each event
 * is applied with a single `TransactWriteItems`: a conditional put of the
 * eventId marker and an `UpdateItem` `ADD` on the numeric counters and the
 * distinct user-id string set. The condition makes redelivered events no-ops
 * and the `ADD` semantics make concurrent same-date updates commutative, so
 * neither duplicates nor races corrupt the rollup. Derived values
 * (`activeUsers`, `netStorageBytes`) are computed on read via
 * [[DailyRollupState.toRollup]] rather than stored.
 */
final class DynamoDbRollupStore(client: DynamoDbClient, tableName: String, dedupeTableName: String)
    extends RollupStore:

  def get(date: String): Option[DailyRollupState] =
    val request = GetItemRequest
      .builder()
      .tableName(tableName)
      .key(Map("date" -> AttributeValue.fromS(date)).asJava)
      .consistentRead(true)
      .build()
    val item = client.getItem(request).item()
    if item == null || item.isEmpty then None
    else
      Some(
        DailyRollupState(
          date = item.get("date").s(),
          totalEvents = n(item, "totalEvents"),
          userIds = Option(item.get("userIds")).map(_.ss().asScala.toSet).getOrElse(Set.empty),
          documentsCreated = n(item, "documentsCreated"),
          documentsViewed = n(item, "documentsViewed"),
          documentsEdited = n(item, "documentsEdited"),
          filesUploaded = n(item, "filesUploaded"),
          filesDownloaded = n(item, "filesDownloaded"),
          collabSessions = n(item, "collabSessions"),
          storageAllocatedBytes = n(item, "storageAllocatedBytes"),
          storageReleasedBytes = n(item, "storageReleasedBytes")
        )
      )

  def applyEvent(event: AnalyticsEvent): Boolean =
    val date = IncrementalUsageRollup.dateOf(event.timestamp)
    val delta = DailyRollupState.empty(date)(event)
    val expiresAt = Instant.now().plus(DynamoDbRollupStore.DedupeTtl).getEpochSecond
    val marker = Put
      .builder()
      .tableName(dedupeTableName)
      .item(
        Map(
          "eventId" -> AttributeValue.fromS(event.eventId),
          "expiresAt" -> AttributeValue.fromN(expiresAt.toString)
        ).asJava
      )
      .conditionExpression("attribute_not_exists(eventId)")
      .build()
    val request = TransactWriteItemsRequest
      .builder()
      .transactItems(
        TransactWriteItem.builder().put(marker).build(),
        TransactWriteItem.builder().update(updateFor(delta)).build()
      )
      .build()
    try
      client.transactWriteItems(request)
      true
    catch
      case ex: TransactionCanceledException
          if ex.cancellationReasons().asScala.exists(_.code() == "ConditionalCheckFailed") =>
        false

  private def updateFor(delta: DailyRollupState): Update =
    val counters = List(
      "totalEvents" -> delta.totalEvents,
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
    // DynamoDB string sets cannot be empty.
    if delta.userIds.nonEmpty then
      adds += "#userIds :userIds"
      values(":userIds") = AttributeValue.fromSs(delta.userIds.toList.sorted.asJava)
    val names = (counters.map(_._1) ++ (if delta.userIds.nonEmpty then List("userIds") else Nil))
      .map(name => s"#$name" -> name)
      .toMap
    Update
      .builder()
      .tableName(tableName)
      .key(Map("date" -> AttributeValue.fromS(delta.date)).asJava)
      .updateExpression("ADD " + adds.mkString(", "))
      .expressionAttributeNames(names.asJava)
      .expressionAttributeValues(values.toMap.asJava)
      .build()

  private def n(item: java.util.Map[String, AttributeValue], key: String): Long =
    Option(item.get(key)).map(_.n().toLong).getOrElse(0L)
