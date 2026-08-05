package com.otterworks.analytics.event

import com.otterworks.analytics.batch.{EventLoader, UsageRollupAggregator, UsageRollupJob}
import com.otterworks.analytics.model.*
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.time.Instant
import scala.util.Random

class IncrementalUsageRollupSpec extends AnyFlatSpec with Matchers:

  private def event(
      eventType: String,
      userId: String,
      timestamp: String,
      resourceType: String = "document",
      metadata: Map[String, String] = Map.empty
  ): AnalyticsEvent =
    AnalyticsEvent(
      eventId = s"$eventType-$userId-$timestamp",
      eventType = eventType,
      userId = userId,
      resourceId = "res-1",
      resourceType = resourceType,
      metadata = metadata,
      timestamp = Instant.parse(timestamp)
    )

  "IncrementalUsageRollup" should "upsert one state per UTC day, sorted ascending" in {
    val events = Seq(
      event(EventType.DocumentCreated, "u1", "2024-03-02T10:00:00Z"),
      event(EventType.DocumentCreated, "u2", "2024-03-01T10:00:00Z"),
      event(EventType.DocumentViewed, "u1", "2024-03-01T23:59:59Z")
    )

    val states = IncrementalUsageRollup.applyAll(Map.empty, events)

    IncrementalUsageRollup.rollups(states).map(_.date) shouldBe List("2024-03-01", "2024-03-02")
  }

  it should "keep activeUsers a distinct count under incremental upserts" in {
    val events = Seq(
      event(EventType.DocumentCreated, "u1", "2024-03-01T01:00:00Z"),
      event(EventType.DocumentViewed, "u1", "2024-03-01T02:00:00Z"),
      event(EventType.DocumentViewed, "u2", "2024-03-01T03:00:00Z"),
      event(EventType.DocumentViewed, "u1", "2024-03-01T04:00:00Z")
    )

    val List(day) = IncrementalUsageRollup.rollups(IncrementalUsageRollup.applyAll(Map.empty, events))

    day.totalEvents shouldBe 4L
    day.activeUsers shouldBe 2L
  }

  it should "sum storage allocated/released bytes and compute the net incrementally" in {
    val events = Seq(
      event(EventType.StorageAllocated, "u1", "2024-03-01T01:00:00Z", "file", Map("bytes" -> "1000")),
      event(EventType.StorageAllocated, "u2", "2024-03-01T02:00:00Z", "file", Map("bytes" -> "500")),
      event(EventType.StorageReleased, "u1", "2024-03-01T03:00:00Z", "file", Map("bytes" -> "200"))
    )

    val List(day) = IncrementalUsageRollup.rollups(IncrementalUsageRollup.applyAll(Map.empty, events))

    day.storageAllocatedBytes shouldBe 1500L
    day.storageReleasedBytes shouldBe 200L
    day.netStorageBytes shouldBe 1300L
  }

  it should "tolerate missing or malformed byte metadata like the batch aggregator" in {
    val events = Seq(
      event(EventType.StorageAllocated, "u1", "2024-03-01T01:00:00Z", "file", Map.empty),
      event(EventType.StorageAllocated, "u2", "2024-03-01T02:00:00Z", "file", Map("bytes" -> "not-a-number"))
    )

    val List(day) = IncrementalUsageRollup.rollups(IncrementalUsageRollup.applyAll(Map.empty, events))

    day.storageAllocatedBytes shouldBe 0L
  }

  it should "combine same-date states with counter sums and distinct user union" in {
    val left = IncrementalUsageRollup.applyAll(
      Map.empty,
      Seq(
        event(EventType.DocumentCreated, "u1", "2024-03-01T01:00:00Z"),
        event(EventType.StorageAllocated, "u2", "2024-03-01T02:00:00Z", "file", Map("bytes" -> "100"))
      )
    )("2024-03-01")
    val right = IncrementalUsageRollup.applyAll(
      Map.empty,
      Seq(
        event(EventType.DocumentViewed, "u1", "2024-03-01T03:00:00Z"),
        event(EventType.StorageReleased, "u3", "2024-03-01T04:00:00Z", "file", Map("bytes" -> "40"))
      )
    )("2024-03-01")

    val day = left.combine(right).toRollup

    day.totalEvents shouldBe 4L
    day.activeUsers shouldBe 3L
    day.documentsCreated shouldBe 1L
    day.documentsViewed shouldBe 1L
    day.netStorageBytes shouldBe 60L
  }

  it should "reproduce the three deterministic daily rollups from the bundled seed" in {
    val events = EventLoader.fromResource(UsageRollupJob.DefaultInput)

    val rollups = IncrementalUsageRollup.rollups(IncrementalUsageRollup.applyAll(Map.empty, events))

    rollups.map(_.date) shouldBe List("2024-03-01", "2024-03-02", "2024-03-03")
    rollups.foreach { day =>
      day.totalEvents shouldBe 55L
      day.activeUsers shouldBe 8L
      day.storageAllocatedBytes shouldBe 6L * 1024 * 1024
      day.storageReleasedBytes shouldBe 2L * 1024 * 1024
      day.netStorageBytes shouldBe 4L * 1024 * 1024
    }
  }

  it should "match the batch aggregator exactly, regardless of event arrival order" in {
    val events = EventLoader.fromResource(UsageRollupJob.DefaultInput)
    val batchRollups = UsageRollupAggregator.rollup(events)

    val shuffled = new Random(42).shuffle(events)
    val incremental = IncrementalUsageRollup.rollups(IncrementalUsageRollup.applyAll(Map.empty, shuffled))

    incremental shouldBe batchRollups
  }

  it should "yield the same result when events arrive split across many small batches" in {
    val events = EventLoader.fromResource(UsageRollupJob.DefaultInput)
    val batchRollups = UsageRollupAggregator.rollup(events)

    val incremental = events
      .grouped(7)
      .foldLeft(Map.empty[String, DailyRollupState])(IncrementalUsageRollup.applyAll)

    IncrementalUsageRollup.rollups(incremental) shouldBe batchRollups
  }
