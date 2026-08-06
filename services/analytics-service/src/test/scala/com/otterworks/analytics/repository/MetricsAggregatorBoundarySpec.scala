package com.otterworks.analytics.repository

import com.otterworks.analytics.model.*
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.time.{Duration, Instant}

/**
 * Boundary, negative and window-edge cases for [[MetricsAggregator]] (WP-12).
 *
 * `periodToCutoff` reads `Instant.now()` internally and is not injectable, so the
 * fixtures below are anchored to a cutoff captured *before* the call under test.
 * Because the cutoff only ever moves forward, "at the cutoff" and "before the
 * cutoff" are excluded deterministically; the "inside the window" case carries a
 * five-minute margin so it cannot race the clock.
 */
class MetricsAggregatorBoundarySpec extends AnyFlatSpec with Matchers:

  private def event(
      eventType: String,
      userId: String = "u1",
      timestamp: Instant = Instant.parse("2024-03-01T00:00:00Z"),
      eventId: String = "",
      resourceId: String = "res-1",
      resourceType: String = "document",
      metadata: Map[String, String] = Map.empty
  ): AnalyticsEvent =
    AnalyticsEvent(
      eventId = if eventId.nonEmpty then eventId else s"$eventType-$userId-$resourceId-$timestamp",
      eventType = eventType,
      userId = userId,
      resourceId = resourceId,
      resourceType = resourceType,
      metadata = metadata,
      timestamp = timestamp
    )

  /** A timestamp comfortably inside every supported period window. */
  private def inWindow: Instant = Instant.now().minusSeconds(60)

  // ---- empty input window ----

  "An empty event window" should "yield a zeroed dashboard summary" in {
    val summary = MetricsAggregator.dashboardSummary(Nil, "7d")

    summary.period shouldBe "7d"
    summary.dailyActiveUsers shouldBe 0L
    summary.documentsCreated shouldBe 0L
    summary.filesUploaded shouldBe 0L
    summary.storageUsedBytes shouldBe 0L
    summary.collabSessions shouldBe 0L
    summary.totalEvents shouldBe 0L
  }

  it should "yield empty top content, active users, storage and export payloads" in {
    MetricsAggregator.topContent(Nil, "documents", "7d", 10).items shouldBe empty
    MetricsAggregator.activeUsers(Nil, "7d").count shouldBe 0L
    MetricsAggregator.activeUsers(Nil, "7d").users shouldBe empty
    MetricsAggregator.storageUsage(Nil, None).totalStorageBytes shouldBe 0L
    MetricsAggregator.storageUsage(Nil, None).breakdownByType shouldBe empty
    MetricsAggregator.exportData(Nil, "7d") shouldBe empty
  }

  it should "yield a zeroed activity record for an unknown user" in {
    val activity = MetricsAggregator.userActivity(Seq(event(EventType.DocumentViewed, userId = "someone")), "nobody")

    activity.userId shouldBe "nobody"
    activity.totalEvents shouldBe 0L
    activity.lastActiveAt shouldBe None
    activity.recentEvents shouldBe empty
  }

  it should "yield zeroed document stats for an unknown document" in {
    val stats = MetricsAggregator.documentStats(Seq(event(EventType.DocumentViewed)), "doc-does-not-exist")

    stats.views shouldBe 0L
    stats.uniqueViewers shouldBe 0L
    stats.lastViewedAt shouldBe None
    stats.lastEditedAt shouldBe None
  }

  // ---- period window edge (cutoff is exclusive) ----

  "The period cutoff" should "exclude an event exactly at the cutoff and before it" in {
    val cutoff = MetricsAggregator.periodToCutoff("7d")
    val events = Seq(
      event(EventType.DocumentViewed, timestamp = cutoff.minusNanos(1), eventId = "before-cutoff"),
      event(EventType.DocumentViewed, timestamp = cutoff, eventId = "at-cutoff"),
      event(EventType.DocumentViewed, timestamp = cutoff.plusSeconds(300), eventId = "after-cutoff")
    )

    MetricsAggregator.dashboardSummary(events, "7d").totalEvents shouldBe 1L
    MetricsAggregator.exportData(events, "7d").map(_("event_id")) shouldBe List("after-cutoff")
  }

  it should "widen monotonically from daily through 90d" in {
    val daily = MetricsAggregator.periodToCutoff("daily")
    val sevenDay = MetricsAggregator.periodToCutoff("7d")
    val thirtyDay = MetricsAggregator.periodToCutoff("30d")
    val ninetyDay = MetricsAggregator.periodToCutoff("90d")

    daily should be > sevenDay
    sevenDay should be > thirtyDay
    thirtyDay should be > ninetyDay
    // The cutoffs are computed microseconds apart, so allow a few seconds of slack.
    spanInDays(sevenDay, daily) shouldBe 6.0 +- 0.01
    spanInDays(ninetyDay, daily) shouldBe 89.0 +- 0.01
  }

  private def spanInDays(from: Instant, to: Instant): Double =
    Duration.between(from, to).getSeconds.toDouble / 86400.0

  it should "treat weekly as 7d, monthly as 30d, and anything unknown as 7d" in {
    val reference = MetricsAggregator.periodToCutoff("7d")

    // Cutoffs are computed a few microseconds apart, so compare to second precision.
    Duration.between(reference, MetricsAggregator.periodToCutoff("weekly")).toSeconds shouldBe 0L
    Duration.between(reference, MetricsAggregator.periodToCutoff("")).toSeconds shouldBe 0L
    Duration.between(reference, MetricsAggregator.periodToCutoff("not-a-period")).toSeconds shouldBe 0L
    Duration
      .between(MetricsAggregator.periodToCutoff("30d"), MetricsAggregator.periodToCutoff("monthly"))
      .toSeconds shouldBe 0L
  }

  it should "apply the unknown-period fallback to the events actually returned" in {
    val cutoff = MetricsAggregator.periodToCutoff("7d")
    val events = Seq(
      event(EventType.DocumentViewed, timestamp = cutoff.minusSeconds(86400), eventId = "eight-days-old"),
      event(EventType.DocumentViewed, timestamp = inWindow, eventId = "recent")
    )

    MetricsAggregator.dashboardSummary(events, "garbage").totalEvents shouldBe 1L
  }

  // ---- FINDING: storageUsedBytes ignores the requested period ----

  "The dashboard storage figure" should "count storage events from outside the requested window" in {
    // FINDING (WP-12, judged genuine): `dashboardSummary` computes every counter
    // from the period-filtered events except `storageUsedBytes`, which folds over
    // the unfiltered `events`. A "last 7 days" dashboard therefore reports
    // all-time storage. Pinned here rather than fixed -- this is a test-only
    // package and the fix belongs in production code.
    val cutoff = MetricsAggregator.periodToCutoff("7d")
    val events = Seq(
      event(
        EventType.StorageAllocated,
        timestamp = cutoff.minusSeconds(86400 * 30),
        eventId = "ancient",
        metadata = Map("bytes" -> "4096")
      )
    )

    val summary = MetricsAggregator.dashboardSummary(events, "7d")

    summary.totalEvents shouldBe 0L
    summary.storageUsedBytes shouldBe 4096L
  }

  // ---- topContent limit boundary trio ----

  private def fiveResources: Seq[AnalyticsEvent] =
    (1 to 5).flatMap { r =>
      (1 to r).map { i =>
        event(
          EventType.DocumentViewed,
          userId = s"u$i",
          timestamp = inWindow,
          eventId = s"r$r-$i",
          resourceId = s"doc-$r"
        )
      }
    }

  "topContent" should "return limit-1, limit and limit+1 items around the data size" in {
    def sizeFor(limit: Int): Int = MetricsAggregator.topContent(fiveResources, "documents", "7d", limit).items.size

    sizeFor(4) shouldBe 4 // limit - 1
    sizeFor(5) shouldBe 5 // limit == number of distinct resources
    sizeFor(6) shouldBe 5 // limit + 1 cannot invent rows
  }

  it should "return nothing for a zero or negative limit" in {
    MetricsAggregator.topContent(fiveResources, "documents", "7d", 0).items shouldBe empty
    MetricsAggregator.topContent(fiveResources, "documents", "7d", -1).items shouldBe empty
  }

  it should "rank by event count and carry unique users" in {
    val items = MetricsAggregator.topContent(fiveResources, "documents", "7d", 5).items

    items.map(_.resourceId) shouldBe List("doc-5", "doc-4", "doc-3", "doc-2", "doc-1")
    items.map(_.eventCount) shouldBe List(5L, 4L, 3L, 2L, 1L)
    items.head.uniqueUsers shouldBe 5L
  }

  it should "fall back to both content types for an unrecognised contentType" in {
    val events = Seq(
      event(EventType.DocumentViewed, timestamp = inWindow, eventId = "d", resourceId = "doc-1"),
      event(EventType.FileDownloaded, timestamp = inWindow, eventId = "f", resourceId = "file-1", resourceType = "file")
    )

    MetricsAggregator.topContent(events, "documents", "7d", 10).items.map(_.resourceId) shouldBe List("doc-1")
    MetricsAggregator.topContent(events, "files", "7d", 10).items.map(_.resourceId) shouldBe List("file-1")
    MetricsAggregator.topContent(events, "everything", "7d", 10).items.map(_.resourceId).sorted shouldBe
      List("doc-1", "file-1")
  }

  it should "ignore an unknown resourceType entirely" in {
    val events = Seq(
      event(EventType.DocumentViewed, timestamp = inWindow, resourceId = "x-1", resourceType = "spreadsheet")
    )

    MetricsAggregator.topContent(events, "everything", "7d", 10).items shouldBe empty
  }

  it should "fall back to the resourceId when no title metadata is present" in {
    val titled = event(
      EventType.DocumentViewed,
      timestamp = inWindow,
      eventId = "t",
      resourceId = "doc-1",
      metadata = Map("title" -> "Quarterly Plan")
    )
    val untitled = event(EventType.DocumentViewed, timestamp = inWindow, eventId = "u", resourceId = "doc-2")

    val items = MetricsAggregator.topContent(Seq(titled, untitled), "documents", "7d", 10).items
    items.map(i => i.resourceId -> i.title).toMap shouldBe
      Map("doc-1" -> "Quarterly Plan", "doc-2" -> "doc-2")
  }

  // ---- userActivity recentEvents cap of 20 ----

  "userActivity" should "cap recentEvents at 20 across the 19/20/21 boundary" in {
    def recentCountFor(n: Int): Int =
      val events = (1 to n).map { i =>
        event(EventType.DocumentViewed, timestamp = inWindow.minusSeconds(i.toLong), eventId = s"e-$i")
      }
      MetricsAggregator.userActivity(events, "u1").recentEvents.size

    recentCountFor(19) shouldBe 19
    recentCountFor(20) shouldBe 20
    recentCountFor(21) shouldBe 20
  }

  it should "return recentEvents newest first and keep the full total" in {
    val base = Instant.parse("2024-03-01T00:00:00Z")
    val events = (1 to 25).map { i =>
      event(EventType.DocumentViewed, timestamp = base.plusSeconds(i.toLong), eventId = s"e-$i")
    }

    val activity = MetricsAggregator.userActivity(events, "u1")

    activity.totalEvents shouldBe 25L
    activity.lastActiveAt shouldBe Some(base.plusSeconds(25).toString)
    activity.recentEvents.map(_.eventId).head shouldBe "e-25"
    activity.recentEvents.map(_.eventId).last shouldBe "e-6"
  }

  // ---- storageUsage clamping ----

  "storageUsage" should "clamp a net-negative balance to zero at the release boundary" in {
    def totalFor(released: Long): Long =
      val events = Seq(
        event(EventType.StorageAllocated, eventId = "alloc", metadata = Map("bytes" -> "100")),
        event(EventType.StorageReleased, eventId = "rel", metadata = Map("bytes" -> released.toString))
      )
      MetricsAggregator.storageUsage(events, None).totalStorageBytes

    totalFor(99) shouldBe 1L // release - 1
    totalFor(100) shouldBe 0L // release == allocation
    totalFor(101) shouldBe 0L // release + 1, clamped rather than negative
  }

  it should "scope the balance to a single user when one is given" in {
    val events = Seq(
      event(EventType.StorageAllocated, userId = "a", eventId = "a1", metadata = Map("bytes" -> "10")),
      event(EventType.StorageAllocated, userId = "b", eventId = "b1", metadata = Map("bytes" -> "70"))
    )

    MetricsAggregator.storageUsage(events, Some("a")).totalStorageBytes shouldBe 10L
    MetricsAggregator.storageUsage(events, Some("nobody")).totalStorageBytes shouldBe 0L
    MetricsAggregator.storageUsage(events, None).totalStorageBytes shouldBe 80L
  }

  it should "treat malformed byte metadata as zero in the total but drop it from the breakdown" in {
    val events = Seq(
      event(EventType.StorageAllocated, eventId = "ok", metadata = Map("bytes" -> "10")),
      event(EventType.StorageAllocated, eventId = "junk", metadata = Map("bytes" -> "not-a-number")),
      event(EventType.StorageAllocated, eventId = "missing")
    )

    MetricsAggregator.storageUsage(events, None).totalStorageBytes shouldBe 10L
    MetricsAggregator.storageUsage(events, None).breakdownByType shouldBe Map("document" -> 10L)
  }

  // ---- duplicate events ----

  "Duplicate events" should "inflate every count except the distinct-user ones" in {
    // FINDING (WP-12): the aggregations have no idempotency key, so a redelivered
    // event is counted twice everywhere except where `distinct` happens to hide it.
    val e = event(EventType.DocumentViewed, timestamp = inWindow, eventId = "dup", resourceId = "doc-1")
    val events = Seq(e, e)

    MetricsAggregator.dashboardSummary(events, "7d").totalEvents shouldBe 2L
    MetricsAggregator.dashboardSummary(events, "7d").dailyActiveUsers shouldBe 1L
    MetricsAggregator.documentStats(events, "doc-1").views shouldBe 2L
    MetricsAggregator.documentStats(events, "doc-1").uniqueViewers shouldBe 1L
    MetricsAggregator.topContent(events, "documents", "7d", 10).items.head.eventCount shouldBe 2L
    MetricsAggregator.topContent(events, "documents", "7d", 10).items.head.uniqueUsers shouldBe 1L
  }

  it should "leave every aggregation unchanged when the same input is re-read" in {
    val events = Seq(
      event(EventType.DocumentViewed, timestamp = inWindow, eventId = "a"),
      event(EventType.FileUploaded, timestamp = inWindow, eventId = "b", resourceType = "file")
    )

    MetricsAggregator.dashboardSummary(events, "7d") shouldBe MetricsAggregator.dashboardSummary(events, "7d")
    MetricsAggregator.activeUsers(events, "7d") shouldBe MetricsAggregator.activeUsers(events, "7d")
    MetricsAggregator.exportData(events, "7d") shouldBe MetricsAggregator.exportData(events, "7d")
  }

  // ---- very large windows ----

  "A very large event set" should "aggregate without truncating counts" in {
    val events = (0 until 20000).map { i =>
      event(
        EventType.DocumentViewed,
        userId = s"u${i % 250}",
        timestamp = inWindow.minusSeconds(i.toLong),
        eventId = s"big-$i",
        resourceId = s"doc-${i % 40}"
      )
    }

    val summary = MetricsAggregator.dashboardSummary(events, "90d")
    summary.totalEvents shouldBe 20000L
    summary.dailyActiveUsers shouldBe 250L

    val active = MetricsAggregator.activeUsers(events, "90d")
    active.count shouldBe 250L
    active.users.map(_.eventCount) shouldBe active.users.map(_.eventCount).sorted.reverse

    MetricsAggregator.topContent(events, "documents", "90d", 10).items should have size 10
    MetricsAggregator.exportData(events, "90d") should have size 20000
  }

  it should "keep exportData sorted newest first over the whole window" in {
    val events = (0 until 500).map { i =>
      event(EventType.DocumentViewed, timestamp = inWindow.minusSeconds(i.toLong * 3600), eventId = s"x-$i")
    }

    val exported = MetricsAggregator.exportData(events.reverse, "90d").map(_("timestamp"))

    exported shouldBe exported.sorted.reverse
    exported should have size 500
  }
