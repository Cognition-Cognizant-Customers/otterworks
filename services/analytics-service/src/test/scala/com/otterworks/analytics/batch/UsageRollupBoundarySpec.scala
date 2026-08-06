package com.otterworks.analytics.batch

import com.otterworks.analytics.model.*
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.time.{Instant, LocalDateTime, ZoneId, ZoneOffset}

/**
 * Boundary, timezone and idempotency edges of the nightly usage rollup.
 *
 * The rollup buckets by **UTC calendar day** ([[UsageRollupAggregator]] uses
 * `ZoneOffset.UTC` explicitly). Every expectation below is therefore derived
 * from a pinned zone or a literal `Z` instant — never from the JVM default
 * zone — so the suite gives the same result on any runner.
 */
class UsageRollupBoundarySpec extends AnyFlatSpec with Matchers:

  private val NewYork = ZoneId.of("America/New_York")
  private val Tokyo = ZoneId.of("Asia/Tokyo")

  /** UTC-day bucket edges used by the boundary trio. */
  private val BucketStart = Instant.parse("2024-03-01T00:00:00Z")
  private val BucketEnd = Instant.parse("2024-03-02T00:00:00Z")

  private def eventAt(
      instant: Instant,
      eventType: String = EventType.DocumentCreated,
      userId: String = "u1",
      eventId: String = "",
      resourceType: String = "document",
      metadata: Map[String, String] = Map.empty
  ): AnalyticsEvent =
    AnalyticsEvent(
      eventId = if eventId.nonEmpty then eventId else s"$eventType@$instant",
      eventType = eventType,
      userId = userId,
      resourceId = "res-1",
      resourceType = resourceType,
      metadata = metadata,
      timestamp = instant
    )

  /** Half-open aggregation window `[from, to)`, the shape the batch job selects with. */
  private def windowed(events: Seq[AnalyticsEvent], from: Instant, to: Instant): Seq[AnalyticsEvent] =
    events.filter(e => !e.timestamp.isBefore(from) && e.timestamp.isBefore(to))

  // ---------------------------------------------------------------- windows

  "A rollup over an empty input window" should "produce no rollups and an unbounded report window" in {
    val events = Seq(eventAt(Instant.parse("2024-03-05T12:00:00Z")))
    val selected = windowed(events, BucketStart, BucketEnd)

    selected shouldBe empty
    UsageRollupAggregator.rollup(selected) shouldBe empty

    val report = UsageRollupJob.buildReport(selected, "empty-window", Instant.parse("2024-03-02T02:00:00Z"))
    report.dayCount shouldBe 0L
    report.totalEvents shouldBe 0L
    report.windowStart shouldBe None
    report.windowEnd shouldBe None
    report.rollups shouldBe empty
  }

  it should "still emit a well-formed report document for zero events" in {
    val report = UsageRollupJob.buildReport(Seq.empty, "seed", Instant.parse("2024-03-02T02:00:00Z"))
    report.generatedAt shouldBe "2024-03-02T02:00:00Z"
    report.source shouldBe "seed"
  }

  "A window containing exactly one event" should "produce exactly one rollup with unit counters" in {
    val one = Seq(eventAt(Instant.parse("2024-03-01T09:30:00Z"), EventType.FileUploaded, resourceType = "file"))

    val Seq(day) = UsageRollupAggregator.rollup(one)

    day.date shouldBe "2024-03-01"
    day.totalEvents shouldBe 1L
    day.activeUsers shouldBe 1L
    day.filesUploaded shouldBe 1L
    day.documentsCreated shouldBe 0L
    day.netStorageBytes shouldBe 0L
  }

  it should "report a single-day window whose start equals its end" in {
    val one = Seq(eventAt(Instant.parse("2024-03-01T09:30:00Z")))
    val report = UsageRollupJob.buildReport(one, "seed", Instant.parse("2024-03-02T00:00:00Z"))

    report.dayCount shouldBe 1L
    report.windowStart shouldBe Some("2024-03-01")
    report.windowEnd shouldBe Some("2024-03-01")
  }

  "A window whose end precedes its start" should "select nothing rather than inverting the range" in {
    val events = Seq(
      eventAt(Instant.parse("2024-03-01T00:00:00Z")),
      eventAt(Instant.parse("2024-03-05T00:00:00Z")),
      eventAt(Instant.parse("2024-03-10T00:00:00Z"))
    )

    val inverted = windowed(events, from = Instant.parse("2024-03-10T00:00:00Z"), to = Instant.parse("2024-03-01T00:00:00Z"))

    inverted shouldBe empty
    UsageRollupAggregator.rollup(inverted) shouldBe empty
  }

  "A very large aggregation window" should "keep one rollup per day, ascending, across decades" in {
    val events = Seq(
      eventAt(Instant.parse("2100-01-01T00:00:00Z"), userId = "u3"),
      eventAt(Instant.parse("1970-01-01T00:00:00Z"), userId = "u1"),
      eventAt(Instant.parse("2024-02-29T23:59:59Z"), userId = "u2")
    )

    val rollups = UsageRollupAggregator.rollup(events)

    rollups.map(_.date) shouldBe List("1970-01-01", "2024-02-29", "2100-01-01")
    rollups.map(_.totalEvents) shouldBe List(1L, 1L, 1L)
  }

  it should "sum a full year of daily events into 366 leap-year buckets" in {
    val start = Instant.parse("2024-01-01T12:00:00Z")
    val events = (0 until 366).map(d => eventAt(start.plusSeconds(d.toLong * 86400), eventId = s"e-$d"))

    val rollups = UsageRollupAggregator.rollup(events)

    rollups.size shouldBe 366
    rollups.head.date shouldBe "2024-01-01"
    rollups.last.date shouldBe "2024-12-31"
    rollups.map(_.totalEvents).sum shouldBe 366L
  }

  // -------------------------------------------------------- bucket boundary

  "An event at bucketStart" should "land in that bucket's day" in {
    val Seq(day) = UsageRollupAggregator.rollup(Seq(eventAt(BucketStart)))
    day.date shouldBe "2024-03-01"
  }

  "An event one nanosecond before bucketEnd" should "still land in the opening bucket" in {
    val Seq(day) = UsageRollupAggregator.rollup(Seq(eventAt(BucketEnd.minusNanos(1))))
    day.date shouldBe "2024-03-01"
  }

  "An event exactly at bucketEnd" should "land in the next bucket, not the closing one" in {
    val Seq(day) = UsageRollupAggregator.rollup(Seq(eventAt(BucketEnd)))
    day.date shouldBe "2024-03-02"
  }

  "The bucket-boundary trio together" should "split into exactly two adjacent days" in {
    val rollups = UsageRollupAggregator.rollup(
      Seq(
        eventAt(BucketStart, eventId = "at-start"),
        eventAt(BucketEnd.minusNanos(1), eventId = "just-before-end"),
        eventAt(BucketEnd, eventId = "at-end")
      )
    )

    rollups.map(d => d.date -> d.totalEvents) shouldBe List("2024-03-01" -> 2L, "2024-03-02" -> 1L)
  }

  // ------------------------------------------------------------ DST / zones

  "Midnight in a non-UTC zone" should "not open a new UTC bucket" in {
    // 2024-03-01T00:00 in Tokyo is 2024-02-29T15:00Z — the previous UTC day.
    val tokyoMidnight = LocalDateTime.parse("2024-03-01T00:00:00").atZone(Tokyo).toInstant
    tokyoMidnight shouldBe Instant.parse("2024-02-29T15:00:00Z")

    val Seq(day) = UsageRollupAggregator.rollup(Seq(eventAt(tokyoMidnight)))
    day.date shouldBe "2024-02-29"
  }

  it should "put local-evening and UTC-morning events of the same instant range in one UTC day" in {
    // 2024-03-01T20:00 in New York is 2024-03-02T01:00Z.
    val nyEvening = LocalDateTime.parse("2024-03-01T20:00:00").atZone(NewYork).toInstant
    nyEvening shouldBe Instant.parse("2024-03-02T01:00:00Z")

    val rollups = UsageRollupAggregator.rollup(
      Seq(eventAt(nyEvening, eventId = "ny-evening"), eventAt(Instant.parse("2024-03-02T09:00:00Z"), eventId = "utc-morning"))
    )

    rollups.map(_.date) shouldBe List("2024-03-02")
    rollups.head.totalEvents shouldBe 2L
  }

  "Events around the DST spring-forward gap" should "bucket by instant, ignoring the skipped local hour" in {
    // In America/New_York, 2024-03-10T02:30 does not exist; the JVM maps it forward to 03:30 EDT.
    val skippedLocal = LocalDateTime.parse("2024-03-10T02:30:00").atZone(NewYork).toInstant
    skippedLocal shouldBe Instant.parse("2024-03-10T07:30:00Z")

    val lastBeforeGap = LocalDateTime.parse("2024-03-10T01:59:59").atZone(NewYork).toInstant
    lastBeforeGap shouldBe Instant.parse("2024-03-10T06:59:59Z")

    val rollups = UsageRollupAggregator.rollup(
      Seq(eventAt(lastBeforeGap, eventId = "pre-gap"), eventAt(skippedLocal, eventId = "in-gap"))
    )

    rollups.map(_.date) shouldBe List("2024-03-10")
    rollups.head.totalEvents shouldBe 2L
  }

  it should "not merge the two distinct instants of the fall-back repeated hour" in {
    // 2024-11-03T01:30 in New York happens twice: once at -04:00 (EDT), once at -05:00 (EST).
    val firstPass = LocalDateTime.parse("2024-11-03T01:30:00").atZone(NewYork).withEarlierOffsetAtOverlap.toInstant
    val secondPass = LocalDateTime.parse("2024-11-03T01:30:00").atZone(NewYork).withLaterOffsetAtOverlap.toInstant

    firstPass shouldBe Instant.parse("2024-11-03T05:30:00Z")
    secondPass shouldBe Instant.parse("2024-11-03T06:30:00Z")
    firstPass should not be secondPass

    val Seq(day) = UsageRollupAggregator.rollup(
      Seq(eventAt(firstPass, userId = "u1", eventId = "edt"), eventAt(secondPass, userId = "u2", eventId = "est"))
    )

    day.date shouldBe "2024-11-03"
    day.totalEvents shouldBe 2L
    day.activeUsers shouldBe 2L
  }

  it should "split the fall-back day at UTC midnight, not at local midnight" in {
    // 2024-11-02T21:00 EDT is 2024-11-03T01:00Z — the following UTC day.
    val lateOnTheSecond = LocalDateTime.parse("2024-11-02T21:00:00").atZone(NewYork).toInstant
    lateOnTheSecond shouldBe Instant.parse("2024-11-03T01:00:00Z")

    val rollups = UsageRollupAggregator.rollup(
      Seq(
        eventAt(lateOnTheSecond, eventId = "late-local-2nd"),
        eventAt(LocalDateTime.parse("2024-11-02T12:00:00").atZone(NewYork).toInstant, eventId = "midday-local-2nd")
      )
    )

    rollups.map(_.date) shouldBe List("2024-11-02", "2024-11-03")
  }

  "Bucketing" should "depend only on the instant, never on the zone the caller happens to use" in {
    val instant = Instant.parse("2024-06-15T23:45:00Z")
    val viaUtc = instant.atZone(ZoneOffset.UTC).toLocalDate.toString
    val viaTokyo = instant.atZone(Tokyo).toLocalDate.toString

    viaUtc shouldBe "2024-06-15"
    viaTokyo shouldBe "2024-06-16"

    val Seq(day) = UsageRollupAggregator.rollup(Seq(eventAt(instant)))
    day.date shouldBe viaUtc
  }

  // --------------------------------------------------------- idempotency

  "Re-ingesting an event id already present" should "not change the rollup when the loader de-duplicates" in {
    // The durable store enforces uniqueness (analytics_events.event_id UNIQUE, V1 migration),
    // so a correctly-loaded window never carries the same id twice.
    val once = Seq(eventAt(Instant.parse("2024-03-01T08:00:00Z"), eventId = "evt-dup"))
    val deduplicatedTwice = (once ++ once).distinctBy(_.eventId)

    UsageRollupAggregator.rollup(deduplicatedTwice) shouldBe UsageRollupAggregator.rollup(once)
  }

  it should "currently double-count when the same id reaches the aggregator twice (documented behaviour)" in {
    // FINDING (documented, not fixed here): UsageRollupAggregator has no de-duplication
    // of its own. It relies entirely on its input being unique by event id. A replayed
    // NDJSON line or an at-least-once delivery therefore inflates every counter.
    val e = eventAt(Instant.parse("2024-03-01T08:00:00Z"), eventId = "evt-dup")
    val Seq(day) = UsageRollupAggregator.rollup(Seq(e, e))

    day.totalEvents shouldBe 2L
    day.documentsCreated shouldBe 2L
    day.activeUsers shouldBe 1L // distinct-by-user still collapses
  }

  ignore should "de-duplicate by event id so at-least-once delivery is idempotent (see finding: no dedupe in UsageRollupAggregator)" in {
    val e = eventAt(Instant.parse("2024-03-01T08:00:00Z"), eventId = "evt-dup")
    val Seq(day) = UsageRollupAggregator.rollup(Seq(e, e))
    day.totalEvents shouldBe 1L
  }

  "A duplicated NDJSON line" should "reach the aggregator twice, since the loader does not de-duplicate" in {
    val line =
      """{"eventId":"e1","eventType":"document.created","userId":"u1","resourceId":"d1","resourceType":"document","metadata":{},"timestamp":"2024-03-01T00:00:00Z"}"""
    val events = EventLoader.fromString(s"$line\n$line\n")

    events should have size 2
    events.map(_.eventId).distinct should have size 1
  }

  "buildReport" should "be idempotent for a repeated call on the same window and clock" in {
    val events = Seq(
      eventAt(Instant.parse("2024-03-01T01:00:00Z"), eventId = "a"),
      eventAt(Instant.parse("2024-03-02T01:00:00Z"), eventId = "b")
    )
    val now = Instant.parse("2024-03-03T00:00:00Z")

    UsageRollupJob.buildReport(events, "seed", now) shouldBe UsageRollupJob.buildReport(events, "seed", now)
  }

  it should "not depend on the order events arrive in" in {
    val events = Seq(
      eventAt(Instant.parse("2024-03-02T01:00:00Z"), eventId = "b"),
      eventAt(Instant.parse("2024-03-01T01:00:00Z"), eventId = "a"),
      eventAt(Instant.parse("2024-03-01T23:00:00Z"), eventId = "c")
    )
    val now = Instant.parse("2024-03-03T00:00:00Z")

    UsageRollupJob.buildReport(events.reverse, "seed", now).rollups shouldBe
      UsageRollupJob.buildReport(events, "seed", now).rollups
  }

  // ------------------------------------------------------- storage numerics

  "Storage byte arithmetic in a rollup" should "net to zero when allocations equal releases" in {
    val Seq(day) = UsageRollupAggregator.rollup(
      Seq(
        eventAt(BucketStart, EventType.StorageAllocated, eventId = "a", resourceType = "file", metadata = Map("bytes" -> "4096")),
        eventAt(BucketStart, EventType.StorageReleased, eventId = "r", resourceType = "file", metadata = Map("bytes" -> "4096"))
      )
    )

    day.netStorageBytes shouldBe 0L
  }

  it should "go negative when more is released than allocated (the rollup does not clamp)" in {
    val Seq(day) = UsageRollupAggregator.rollup(
      Seq(eventAt(BucketStart, EventType.StorageReleased, resourceType = "file", metadata = Map("bytes" -> "512")))
    )

    day.storageAllocatedBytes shouldBe 0L
    day.netStorageBytes shouldBe -512L
  }

  it should "treat a negative byte count as the literal value it parses to" in {
    val Seq(day) = UsageRollupAggregator.rollup(
      Seq(eventAt(BucketStart, EventType.StorageAllocated, resourceType = "file", metadata = Map("bytes" -> "-1")))
    )

    day.storageAllocatedBytes shouldBe -1L
  }

  it should "not overflow on a Long.MaxValue allocation followed by an equal release" in {
    val Seq(day) = UsageRollupAggregator.rollup(
      Seq(
        eventAt(BucketStart, EventType.StorageAllocated, eventId = "a", resourceType = "file", metadata = Map("bytes" -> Long.MaxValue.toString)),
        eventAt(BucketStart, EventType.StorageReleased, eventId = "r", resourceType = "file", metadata = Map("bytes" -> Long.MaxValue.toString))
      )
    )

    day.storageAllocatedBytes shouldBe Long.MaxValue
    day.netStorageBytes shouldBe 0L
  }

  it should "fall back to zero for a byte count that overflows Long" in {
    val Seq(day) = UsageRollupAggregator.rollup(
      Seq(eventAt(BucketStart, EventType.StorageAllocated, resourceType = "file", metadata = Map("bytes" -> "9223372036854775808")))
    )

    day.storageAllocatedBytes shouldBe 0L
  }
