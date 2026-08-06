package com.otterworks.analytics.batch

import com.otterworks.analytics.model.*
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.time.{Instant, ZoneId}

/**
 * Boundary and negative cases for the nightly usage rollup (WP-12).
 *
 * The aggregator buckets strictly by UTC calendar day, so the interesting edges
 * are the midnight boundary, the two US/EU daylight-saving transitions, and what
 * happens at the extremes of the input: empty, duplicated, and very large windows.
 */
class UsageRollupBoundarySpec extends AnyFlatSpec with Matchers:

  private def event(
      eventType: String,
      userId: String,
      timestamp: String,
      eventId: String = "",
      resourceType: String = "document",
      metadata: Map[String, String] = Map.empty
  ): AnalyticsEvent =
    AnalyticsEvent(
      eventId = if eventId.nonEmpty then eventId else s"$eventType-$userId-$timestamp",
      eventType = eventType,
      userId = userId,
      resourceId = "res-1",
      resourceType = resourceType,
      metadata = metadata,
      timestamp = Instant.parse(timestamp)
    )

  // ---- empty input window ----

  "An empty input window" should "produce no rollups at all" in {
    UsageRollupAggregator.rollup(Nil) shouldBe empty
  }

  it should "produce a report with an open window and zero totals" in {
    val report = UsageRollupJob.buildReport(Nil, "empty", Instant.parse("2024-03-04T00:00:00Z"))

    report.dayCount shouldBe 0L
    report.totalEvents shouldBe 0L
    report.windowStart shouldBe None
    report.windowEnd shouldBe None
    report.rollups shouldBe empty
  }

  it should "still report a day that contains only events the rollup counts as zero" in {
    // A day whose only event is of an unknown type: the day exists (totalEvents 1)
    // but every typed counter is 0. Distinguishes "no day" from "empty day".
    val Seq(day) = UsageRollupAggregator.rollup(Seq(event("unknown.event", "u1", "2024-03-01T12:00:00Z")))

    day.date shouldBe "2024-03-01"
    day.totalEvents shouldBe 1L
    day.activeUsers shouldBe 1L
    day.documentsCreated shouldBe 0L
    day.filesUploaded shouldBe 0L
    day.netStorageBytes shouldBe 0L
  }

  // ---- UTC midnight bucket edge (boundary trio around 00:00Z) ----

  "The day bucket edge" should "split events on the UTC midnight boundary" in {
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "2024-03-01T23:59:59.999999999Z", eventId = "before"),
      event(EventType.DocumentViewed, "u1", "2024-03-02T00:00:00Z", eventId = "at"),
      event(EventType.DocumentViewed, "u1", "2024-03-02T00:00:00.000000001Z", eventId = "after")
    )

    val rollups = UsageRollupAggregator.rollup(events)

    rollups.map(r => r.date -> r.totalEvents) shouldBe List("2024-03-01" -> 1L, "2024-03-02" -> 2L)
  }

  it should "bucket by UTC, not by the local calendar day of any other zone" in {
    // 23:30Z on 2024-03-01 is already 2024-03-02 in Asia/Tokyo (UTC+9) and still
    // 2024-03-01 in America/New_York. The rollup must ignore both.
    val e = event(EventType.DocumentViewed, "u1", "2024-03-01T23:30:00Z")

    e.timestamp.atZone(ZoneId.of("Asia/Tokyo")).toLocalDate.toString shouldBe "2024-03-02"
    UsageRollupAggregator.rollup(Seq(e)).map(_.date) shouldBe List("2024-03-01")
  }

  it should "keep 29 February as its own bucket in a leap year" in {
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "2024-02-28T23:00:00Z"),
      event(EventType.DocumentViewed, "u1", "2024-02-29T00:00:00Z"),
      event(EventType.DocumentViewed, "u1", "2024-03-01T00:00:00Z")
    )

    UsageRollupAggregator.rollup(events).map(_.date) shouldBe
      List("2024-02-28", "2024-02-29", "2024-03-01")
  }

  // ---- daylight-saving transitions ----

  "A spring-forward DST transition" should "not shift or drop a UTC bucket" in {
    // 2024-03-10 07:00Z is 02:00 EST -> 03:00 EDT in America/New_York: the local
    // hour 02:00-03:00 does not exist. All three events are the same UTC day.
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "2024-03-10T06:59:59Z", eventId = "pre-dst"),
      event(EventType.DocumentViewed, "u2", "2024-03-10T07:00:00Z", eventId = "at-dst"),
      event(EventType.DocumentViewed, "u3", "2024-03-10T07:00:01Z", eventId = "post-dst")
    )

    val Seq(day) = UsageRollupAggregator.rollup(events)

    day.date shouldBe "2024-03-10"
    day.totalEvents shouldBe 3L
    day.activeUsers shouldBe 3L
  }

  "A fall-back DST transition" should "count the repeated local hour twice" in {
    // 2024-11-03 05:00Z and 06:00Z are both 01:00 local in America/New_York
    // (EDT then EST). Bucketing by UTC keeps them as two distinct events.
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "2024-11-03T05:00:00Z", eventId = "edt"),
      event(EventType.DocumentViewed, "u1", "2024-11-03T06:00:00Z", eventId = "est")
    )

    val ny = ZoneId.of("America/New_York")
    events.map(_.timestamp.atZone(ny).toLocalTime.toString).distinct shouldBe List("01:00")

    val Seq(day) = UsageRollupAggregator.rollup(events)
    day.date shouldBe "2024-11-03"
    day.totalEvents shouldBe 2L
  }

  it should "not merge a southern-hemisphere DST day into its neighbour" in {
    // Australia/Sydney enters DST at 2024-10-06 16:00Z (02:00 -> 03:00 local).
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "2024-10-05T16:00:00Z"),
      event(EventType.DocumentViewed, "u1", "2024-10-06T16:00:00Z")
    )

    UsageRollupAggregator.rollup(events).map(_.date) shouldBe List("2024-10-05", "2024-10-06")
  }

  // ---- duplicate events / idempotency ----

  "Duplicate events" should "be counted twice: the rollup does not de-duplicate by eventId" in {
    // FINDING (WP-12): the aggregator has no idempotency key. Replaying the same
    // event -- which an at-least-once SQS delivery makes likely -- double-counts it.
    // This test pins today's behaviour; it is not an endorsement of it.
    val e = event(EventType.DocumentCreated, "u1", "2024-03-01T10:00:00Z", eventId = "dup-1")

    val Seq(day) = UsageRollupAggregator.rollup(Seq(e, e))

    day.totalEvents shouldBe 2L
    day.documentsCreated shouldBe 2L
    day.activeUsers shouldBe 1L // distinct userId still collapses
  }

  it should "double-count duplicated storage bytes as well" in {
    val e = event(
      EventType.StorageAllocated,
      "u1",
      "2024-03-01T10:00:00Z",
      eventId = "dup-bytes",
      metadata = Map("bytes" -> "1024")
    )

    val Seq(day) = UsageRollupAggregator.rollup(Seq(e, e))

    day.storageAllocatedBytes shouldBe 2048L
    day.netStorageBytes shouldBe 2048L
  }

  it should "be idempotent as a function: re-running over the same input is identical" in {
    val events = Seq(
      event(EventType.DocumentCreated, "u1", "2024-03-01T10:00:00Z"),
      event(EventType.FileUploaded, "u2", "2024-03-02T10:00:00Z", resourceType = "file")
    )

    UsageRollupAggregator.rollup(events) shouldBe UsageRollupAggregator.rollup(events)
  }

  it should "not depend on the order events arrive in" in {
    val events = Seq(
      event(EventType.DocumentCreated, "u1", "2024-03-02T10:00:00Z", eventId = "a"),
      event(EventType.DocumentViewed, "u2", "2024-03-01T10:00:00Z", eventId = "b"),
      event(EventType.FileUploaded, "u3", "2024-03-01T11:00:00Z", eventId = "c", resourceType = "file")
    )

    UsageRollupAggregator.rollup(events.reverse) shouldBe UsageRollupAggregator.rollup(events)
  }

  // ---- very large aggregation windows ----

  "A very large aggregation window" should "produce one ascending bucket per day over three years" in {
    val start = Instant.parse("2022-01-01T12:00:00Z")
    val days = 1096 // 2022 + 2023 + 2024 (leap)
    val events = (0 until days).map { i =>
      event(
        EventType.DocumentViewed,
        s"u${i % 50}",
        start.plusSeconds(i.toLong * 86400).toString,
        eventId = s"e-$i"
      )
    }

    val rollups = UsageRollupAggregator.rollup(events)

    rollups should have size days
    rollups.map(_.date) shouldBe rollups.map(_.date).sorted
    rollups.head.date shouldBe "2022-01-01"
    rollups.last.date shouldBe "2024-12-31"
    rollups.map(_.totalEvents).sum shouldBe days.toLong
  }

  it should "cap activeUsers at the number of distinct users, not the event count" in {
    val events = (0 until 5000).map { i =>
      event(EventType.DocumentViewed, s"u${i % 7}", "2024-03-01T00:00:00Z", eventId = s"big-$i")
    }

    val Seq(day) = UsageRollupAggregator.rollup(events)

    day.totalEvents shouldBe 5000L
    day.activeUsers shouldBe 7L
  }

  it should "report a window whose start and end span the whole range" in {
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "2020-01-01T00:00:00Z"),
      event(EventType.DocumentViewed, "u1", "2030-12-31T23:59:59Z")
    )

    val report = UsageRollupJob.buildReport(events, "wide", Instant.parse("2031-01-01T00:00:00Z"))

    report.windowStart shouldBe Some("2020-01-01")
    report.windowEnd shouldBe Some("2030-12-31")
    report.dayCount shouldBe 2L
  }

  // ---- malformed / extreme byte metadata ----

  "Storage byte metadata" should "treat a negative allocation as a negative net" in {
    val events = Seq(
      event(
        EventType.StorageAllocated,
        "u1",
        "2024-03-01T00:00:00Z",
        metadata = Map("bytes" -> "-512")
      )
    )

    val Seq(day) = UsageRollupAggregator.rollup(events)

    day.storageAllocatedBytes shouldBe -512L
    day.netStorageBytes shouldBe -512L
  }

  it should "overflow silently when the summed byte counts exceed Long.MaxValue" in {
    // FINDING (WP-12): the fold uses unchecked Long addition, so a corrupt or
    // hostile `bytes` value wraps around instead of saturating or rejecting.
    // Pinned so a future saturating/validating implementation turns this red.
    val huge = Long.MaxValue.toString
    val events = Seq(
      event(EventType.StorageAllocated, "u1", "2024-03-01T00:00:00Z", eventId = "h1", metadata = Map("bytes" -> huge)),
      event(EventType.StorageAllocated, "u1", "2024-03-01T01:00:00Z", eventId = "h2", metadata = Map("bytes" -> huge))
    )

    val Seq(day) = UsageRollupAggregator.rollup(events)

    day.storageAllocatedBytes shouldBe -2L
  }

  it should "fall back to zero for a byte value that overflows Long" in {
    val events = Seq(
      event(
        EventType.StorageAllocated,
        "u1",
        "2024-03-01T00:00:00Z",
        metadata = Map("bytes" -> "99999999999999999999")
      )
    )

    UsageRollupAggregator.rollup(events).head.storageAllocatedBytes shouldBe 0L
  }
