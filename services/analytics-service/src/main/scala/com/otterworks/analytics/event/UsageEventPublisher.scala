package com.otterworks.analytics.event

import com.otterworks.analytics.model.AnalyticsEvent
import com.otterworks.analytics.model.AnalyticsEventJsonProtocol.given
import org.slf4j.LoggerFactory
import software.amazon.awssdk.services.eventbridge.EventBridgeAsyncClient
import software.amazon.awssdk.services.eventbridge.model.{PutEventsRequest, PutEventsRequestEntry}
import spray.json.*

/**
 * Publishes ingested analytics events onto EventBridge, feeding the
 * event-driven usage-rollup pipeline (EventBridge rule -> SQS -> Lambda).
 * Publishing is best-effort and non-blocking: the event has already been
 * persisted by the service, so a publish failure is logged and never fails
 * the ingest path, and the network round trip happens on the SDK's async
 * client rather than the caller's dispatcher thread.
 */
trait UsageEventPublisher:
  def publish(event: AnalyticsEvent): Unit

/** No-op publisher for tests and deployments without EventBridge. */
object NoopUsageEventPublisher extends UsageEventPublisher:
  def publish(event: AnalyticsEvent): Unit = ()

object UsageEventPublisher:
  /** EventBridge `source` matched by the usage-rollup rule. */
  val Source = "otterworks.analytics"

  /** EventBridge `detail-type` matched by the usage-rollup rule. */
  val DetailType = "AnalyticsEvent"

final class EventBridgeUsageEventPublisher(client: EventBridgeAsyncClient, busName: String) extends UsageEventPublisher:

  private val logger = LoggerFactory.getLogger(getClass)

  def publish(event: AnalyticsEvent): Unit =
    try
      val entry = PutEventsRequestEntry
        .builder()
        .eventBusName(busName)
        .source(UsageEventPublisher.Source)
        .detailType(UsageEventPublisher.DetailType)
        .detail(event.toJson.compactPrint)
        .build()
      client
        .putEvents(PutEventsRequest.builder().entries(entry).build())
        .whenComplete { (response, error) =>
          if error != null then
            logger.warn("Failed to publish usage event {} to EventBridge: {}", event.eventId, error.getMessage)
          else if response.failedEntryCount() > 0 then
            logger.warn("EventBridge rejected usage event {}: {}", event.eventId, response.entries())
        }
    catch
      case ex: Exception =>
        logger.warn("Failed to publish usage event {} to EventBridge: {}", event.eventId, ex.getMessage)
