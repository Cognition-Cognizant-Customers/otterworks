package com.otterworks.analytics.event

import com.otterworks.analytics.batch.{EventLoader, UsageRollupAggregator, UsageRollupJob}
import com.otterworks.analytics.model.*
import com.otterworks.analytics.model.AnalyticsEventJsonProtocol.given
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import spray.json.*

class UsageRollupLambdaHandlerSpec extends AnyFlatSpec with Matchers:

  private def envelope(event: AnalyticsEvent): String =
    JsObject(
      "version" -> JsString("0"),
      "source" -> JsString("otterworks.analytics"),
      "detail-type" -> JsString("AnalyticsEvent"),
      "detail" -> event.toJson
    ).compactPrint

  private def sqsEvent(events: Seq[AnalyticsEvent]): String =
    JsObject(
      "Records" -> JsArray(
        events.map(e => JsObject("messageId" -> JsString(e.eventId), "body" -> JsString(envelope(e)))).toVector
      )
    ).compactPrint

  "UsageRollupLambdaHandler" should "unwrap EventBridge envelopes from SQS record bodies" in {
    val events = EventLoader.fromResource(UsageRollupJob.DefaultInput).take(3)
    UsageRollupLambdaHandler.parseSqsEvents(sqsEvent(events)) shouldBe events
  }

  it should "accept bare analytics events without an EventBridge envelope" in {
    val event = EventLoader.fromResource(UsageRollupJob.DefaultInput).head
    UsageRollupLambdaHandler.parseEnvelope(event.toJson.compactPrint) shouldBe event
  }

  it should "fail on a malformed record body so SQS redrive can route it to the DLQ" in {
    val payload = """{"Records":[{"messageId":"m1","body":"not json"}]}"""
    a[Exception] should be thrownBy UsageRollupLambdaHandler.parseSqsEvents(payload)
  }

  it should "upsert the seed events into three deterministic daily rollups" in {
    val store = InMemoryRollupStore()
    val handler = UsageRollupLambdaHandler(store)
    val events = EventLoader.fromResource(UsageRollupJob.DefaultInput)

    // Deliver in SQS-sized batches of 10, as the event source mapping would.
    events.grouped(10).foreach(batch => handler.process(batch))

    val rollups = IncrementalUsageRollup.rollups(store.snapshot)
    rollups.map(_.date) shouldBe List("2024-03-01", "2024-03-02", "2024-03-03")
    rollups.foreach { day =>
      day.totalEvents shouldBe 55L
      day.activeUsers shouldBe 8L
      day.storageAllocatedBytes shouldBe 6L * 1024 * 1024
      day.storageReleasedBytes shouldBe 2L * 1024 * 1024
      day.netStorageBytes shouldBe 4L * 1024 * 1024
    }
  }

  it should "not lose updates when interleaved handlers share the store" in {
    val store = InMemoryRollupStore()
    val a = UsageRollupLambdaHandler(store)
    val b = UsageRollupLambdaHandler(store)
    val events = EventLoader.fromResource(UsageRollupJob.DefaultInput)

    // Alternate batches between two handler instances, as concurrent Lambda
    // invocations merging deltas into the same date would.
    events.grouped(10).zipWithIndex.foreach { case (batch, i) =>
      (if i % 2 == 0 then a else b).process(batch)
    }

    IncrementalUsageRollup.rollups(store.snapshot) shouldBe UsageRollupAggregator.rollup(events)
  }

  it should "match the deterministic batch aggregation exactly" in {
    val store = InMemoryRollupStore()
    val handler = UsageRollupLambdaHandler(store)
    val events = EventLoader.fromResource(UsageRollupJob.DefaultInput)

    events.grouped(10).foreach(batch => handler.process(batch))

    IncrementalUsageRollup.rollups(store.snapshot) shouldBe UsageRollupAggregator.rollup(events)
  }
