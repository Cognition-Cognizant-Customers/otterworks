package com.otterworks.analytics.event

import software.amazon.awssdk.services.dynamodb.DynamoDbClient
import software.amazon.awssdk.services.dynamodb.model.{AttributeValue, GetItemRequest, UpdateItemRequest}

import scala.jdk.CollectionConverters.*

/**
 * Persistence for per-day incremental rollup state. The Lambda handler folds a
 * batch's events into one delta [[DailyRollupState]] per affected date and
 * merges each delta into the store. Implementations must make `merge` atomic so
 * concurrent invocations touching the same date never lose updates.
 */
trait RollupStore:
  def get(date: String): Option[DailyRollupState]

  /** Atomically fold a delta into the stored state for `delta.date`. */
  def merge(delta: DailyRollupState): Unit

/** In-memory store used by tests and the local comparison harness. */
final class InMemoryRollupStore extends RollupStore:
  private var states: Map[String, DailyRollupState] = Map.empty

  def get(date: String): Option[DailyRollupState] = states.get(date)

  def merge(delta: DailyRollupState): Unit = synchronized {
    val merged = states.get(delta.date) match
      case Some(current) => current.combine(delta)
      case None          => delta
    states = states.updated(delta.date, merged)
  }

  def snapshot: Map[String, DailyRollupState] = states

/**
 * DynamoDB-backed store: one item per calendar date, keyed on `date`. Deltas
 * are applied with a single atomic `UpdateItem` (`ADD` on numeric counters and
 * on the distinct user-id string set), so concurrent Lambda invocations for the
 * same date never overwrite each other's increments. Derived values
 * (`activeUsers`, `netStorageBytes`) are computed on read via
 * [[DailyRollupState.toRollup]] rather than stored.
 */
final class DynamoDbRollupStore(client: DynamoDbClient, tableName: String) extends RollupStore:

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

  def merge(delta: DailyRollupState): Unit =
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
    val request = UpdateItemRequest
      .builder()
      .tableName(tableName)
      .key(Map("date" -> AttributeValue.fromS(delta.date)).asJava)
      .updateExpression("ADD " + adds.mkString(", "))
      .expressionAttributeNames(names.asJava)
      .expressionAttributeValues(values.toMap.asJava)
      .build()
    client.updateItem(request): Unit

  private def n(item: java.util.Map[String, AttributeValue], key: String): Long =
    Option(item.get(key)).map(_.n().toLong).getOrElse(0L)
