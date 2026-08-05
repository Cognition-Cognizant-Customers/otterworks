package com.otterworks.analytics.event

import com.amazonaws.services.lambda.runtime.{Context, RequestStreamHandler}
import com.otterworks.analytics.model.*
import com.otterworks.analytics.model.AnalyticsEventJsonProtocol.given
import org.slf4j.LoggerFactory
import software.amazon.awssdk.services.dynamodb.DynamoDbClient
import spray.json.*

import java.io.{InputStream, OutputStream}
import java.net.URI
import java.nio.charset.StandardCharsets

/**
 * AWS Lambda handler for the event-driven usage-rollup path
 * (EventBridge rule -> SQS queue (with DLQ) -> this Lambda).
 *
 * Each SQS record carries an EventBridge envelope whose `detail` is a raw
 * [[AnalyticsEvent]]. The handler folds the batch's events into per-day
 * [[DailyRollupState]] via [[IncrementalUsageRollup]] and upserts the affected
 * dates in the rollup store, so rollups are fresh within seconds instead of
 * waiting for a nightly batch window. Aggregation semantics (distinct
 * activeUsers, storage allocated/released/net bytes, per-type counts) are
 * identical to the legacy batch
 * [[com.otterworks.analytics.batch.UsageRollupAggregator]].
 *
 * A record that cannot be parsed is left to SQS redrive: the whole batch fails
 * and, after `maxReceiveCount` attempts, lands on the dead-letter queue.
 */
class UsageRollupLambdaHandler(store: RollupStore) extends RequestStreamHandler:

  private val logger = LoggerFactory.getLogger(getClass)

  /** Lambda runtime entrypoint: builds a DynamoDB-backed store from the environment. */
  def this() = this(UsageRollupLambdaHandler.storeFromEnv())

  override def handleRequest(input: InputStream, output: OutputStream, context: Context): Unit =
    val body = new String(input.readAllBytes(), StandardCharsets.UTF_8)
    val events = UsageRollupLambdaHandler.parseSqsEvents(body)
    val updated = process(events)
    logger.info("usage-rollup upsert: events={} datesUpdated={}", events.size, updated.size)
    output.write(s"""{"batchItemFailures":[]}""".getBytes(StandardCharsets.UTF_8))

  /**
   * Incrementally upsert a batch of events; returns the affected dates. Each
   * event is applied individually and idempotently keyed on its eventId, so
   * redelivered events are no-ops and concurrent invocations for the same date
   * never lose each other's updates.
   */
  def process(events: Seq[AnalyticsEvent]): Set[String] =
    events.flatMap { event =>
      if store.applyEvent(event) then Some(IncrementalUsageRollup.dateOf(event.timestamp)) else None
    }.toSet

object UsageRollupLambdaHandler:

  /** Env var naming the DynamoDB rollup table. */
  val TableEnvVar = "ROLLUP_TABLE"

  /** Env var naming the DynamoDB processed-event dedupe table. */
  val DedupeTableEnvVar = "DEDUPE_TABLE"

  /** Optional env var pointing DynamoDB at a local endpoint (LocalStack). */
  val EndpointEnvVar = "ROLLUP_DYNAMODB_ENDPOINT"

  def storeFromEnv(): RollupStore =
    val table = sys.env.getOrElse(TableEnvVar, "otterworks-usage-rollups-dev")
    val dedupeTable = sys.env.getOrElse(DedupeTableEnvVar, "otterworks-usage-rollup-processed-dev")
    val builder = DynamoDbClient.builder()
    sys.env.get(EndpointEnvVar).foreach(url => builder.endpointOverride(URI.create(url)))
    DynamoDbRollupStore(builder.build(), table, dedupeTable)

  /**
   * Parse the SQS event payload the Lambda receives. Each record body is the
   * EventBridge envelope; the analytics event is its `detail` field. A body
   * that is a bare [[AnalyticsEvent]] (no envelope) is also accepted.
   */
  def parseSqsEvents(sqsEventJson: String): List[AnalyticsEvent] =
    val records = sqsEventJson.parseJson.asJsObject.fields.get("Records") match
      case Some(JsArray(elements)) => elements.toList
      case _                       => Nil
    records.map { record =>
      val body = record.asJsObject.fields.get("body") match
        case Some(JsString(s)) => s
        case other             => deserializationError(s"SQS record has no string body: $other")
      parseEnvelope(body)
    }

  /** Unwrap an EventBridge envelope (or accept a bare event). */
  def parseEnvelope(body: String): AnalyticsEvent =
    val json = body.parseJson.asJsObject
    json.fields.get("detail") match
      case Some(detail) => detail.convertTo[AnalyticsEvent]
      case None         => json.convertTo[AnalyticsEvent]
