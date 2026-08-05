package com.otterworks.analytics.event

import software.amazon.awssdk.services.dynamodb.DynamoDbClient
import software.amazon.awssdk.services.dynamodb.model.{AttributeValue, GetItemRequest, PutItemRequest}

import scala.jdk.CollectionConverters.*

/**
 * Persistence for per-day incremental rollup state. The Lambda handler reads
 * the current state for each affected date, folds the batch's events in, and
 * writes the updated state back (an upsert keyed on the calendar date).
 */
trait RollupStore:
  def get(date: String): Option[DailyRollupState]
  def put(state: DailyRollupState): Unit

/** In-memory store used by tests and the local comparison harness. */
final class InMemoryRollupStore extends RollupStore:
  private var states: Map[String, DailyRollupState] = Map.empty

  def get(date: String): Option[DailyRollupState] = states.get(date)

  def put(state: DailyRollupState): Unit =
    states = states.updated(state.date, state)

  def snapshot: Map[String, DailyRollupState] = states

/**
 * DynamoDB-backed store: one item per calendar date, keyed on `date`. The
 * distinct active-user set is persisted as a DynamoDB string set so
 * `activeUsers` stays exact across incremental upserts.
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

  def put(state: DailyRollupState): Unit =
    val rollup = state.toRollup
    val item = scala.collection.mutable.Map[String, AttributeValue](
      "date" -> AttributeValue.fromS(state.date),
      "totalEvents" -> AttributeValue.fromN(state.totalEvents.toString),
      "activeUsers" -> AttributeValue.fromN(rollup.activeUsers.toString),
      "documentsCreated" -> AttributeValue.fromN(state.documentsCreated.toString),
      "documentsViewed" -> AttributeValue.fromN(state.documentsViewed.toString),
      "documentsEdited" -> AttributeValue.fromN(state.documentsEdited.toString),
      "filesUploaded" -> AttributeValue.fromN(state.filesUploaded.toString),
      "filesDownloaded" -> AttributeValue.fromN(state.filesDownloaded.toString),
      "collabSessions" -> AttributeValue.fromN(state.collabSessions.toString),
      "storageAllocatedBytes" -> AttributeValue.fromN(state.storageAllocatedBytes.toString),
      "storageReleasedBytes" -> AttributeValue.fromN(state.storageReleasedBytes.toString),
      "netStorageBytes" -> AttributeValue.fromN(rollup.netStorageBytes.toString)
    )
    // DynamoDB string sets cannot be empty.
    if state.userIds.nonEmpty then item("userIds") = AttributeValue.fromSs(state.userIds.toList.sorted.asJava)
    client.putItem(PutItemRequest.builder().tableName(tableName).item(item.toMap.asJava).build())

  private def n(item: java.util.Map[String, AttributeValue], key: String): Long =
    Option(item.get(key)).map(_.n().toLong).getOrElse(0L)
