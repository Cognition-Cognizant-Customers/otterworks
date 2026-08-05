package com.otterworks.analytics.event

import com.otterworks.analytics.model.AnalyticsEvent
import com.otterworks.analytics.model.AnalyticsEventJsonProtocol.given
import org.slf4j.LoggerFactory
import software.amazon.awssdk.services.eventbridge.EventBridgeClient
import software.amazon.awssdk.services.eventbridge.model.{PutEventsRequest, PutEventsRequestEntry}
import spray.json.*

/**
 * Publishes ingested analytics events onto EventBridge, feeding the
 * event-driven usage-rollup pipeline (EventBridge rule -> SQS -> Lambda).
 * Publishing is best-effort: the event has already been persisted by the
 * service, so a publish failure is logged and never fails the ingest path.
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

final class EventBridgeUsageEventPublisher(client: EventBridgeClient, busName: String) extends UsageEventPublisher:

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
      val response = client.putEvents(PutEventsRequest.builder().entries(entry).build())
      if response.failedEntryCount() > 0 then
        logger.warn("EventBridge rejected usage event {}: {}", event.eventId, response.entries())
    catch
      case ex: Exception =>
        logger.warn("Failed to publish usage event {} to EventBridge: {}", event.eventId, ex.getMessage)
