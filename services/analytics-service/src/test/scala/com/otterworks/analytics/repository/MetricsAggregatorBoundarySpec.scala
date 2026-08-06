package com.otterworks.analytics.repository

import com.otterworks.analytics.model.*
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.time.Instant

/**
 * Limit/threshold boundaries, per-user isolation and empty-input edges of the
 * pure aggregations both repository backends share.
 *
 * Note on windows: [[MetricsAggregator.periodToCutoff]] reads `Instant.now()`
 * internally and no clock can be injected, so an event cannot be placed exactly
 * on a period cutoff from the outside. Window membership is therefore exercised
 * with margins far larger than any plausible test runtime, and the exact
 * `isAfter` boundary is asserted against `periodToCutoff` directly.
 */
class MetricsAggregatorBoundarySpec extends AnyFlatSpec with Matchers:

  private val Now = Instant.now()

  private def event(
      eventType: String,
      userId: String,
      resourceId: String,
      resourceType: String = "document",
      secondsAgo: Long = 60L,
      metadata: Map[String, String] = Map.empty
  ): AnalyticsEvent =
    AnalyticsEvent(
      eventId = s"$eventType-$userId-$resourceId-$secondsAgo",
      eventType = eventType,
      userId = userId,
      resourceId = resourceId,
      resourceType = resourceType,
      metadata = metadata,
      timestamp = Now.minusSeconds(secondsAgo)
    )

  // ------------------------------------------------------ top-content limit

  private val threeDocs: Seq[AnalyticsEvent] = Seq(
    event(EventType.DocumentViewed, "u1", "doc-a", metadata = Map("title" -> "A")),
    event(EventType.DocumentViewed, "u2", "doc-a", secondsAgo = 61),
    event(EventType.DocumentViewed, "u3", "doc-a", secondsAgo = 62),
    event(EventType.DocumentViewed, "u1", "doc-b", secondsAgo = 63),
    event(EventType.DocumentViewed, "u2", "doc-b", secondsAgo = 64),
    event(EventType.DocumentViewed, "u1", "doc-c", secondsAgo = 65)
  )

  "topContent at limit-1" should "drop the least active item" in {
    val items = MetricsAggregator.topContent(threeDocs, "documents", "7d", limit = 2).items
    items.map(_.resourceId) shouldBe List("doc-a", "doc-b")
  }

  "topContent at exactly the available count" should "return every item" in {
    val items = MetricsAggregator.topContent(threeDocs, "documents", "7d", limit = 3).items
    items.map(_.resourceId) shouldBe List("doc-a", "doc-b", "doc-c")
    items.map(_.eventCount) shouldBe List(3L, 2L, 1L)
  }

  "topContent at limit+1" should "return every item without padding" in {
    val items = MetricsAggregator.topContent(threeDocs, "documents", "7d", limit = 4).items
    items should have size 3
  }

  "topContent with limit 0" should "return nothing" in {
    MetricsAggregator.topContent(threeDocs, "documents", "7d", limit = 0).items shouldBe empty
  }

  "topContent with a negative limit" should "return nothing rather than throwing" in {
    MetricsAggregator.topContent(threeDocs, "documents", "7d", limit = -1).items shouldBe empty
    MetricsAggregator.topContent(threeDocs, "documents", "7d", limit = Int.MinValue).items shouldBe empty
  }

  "topContent over no events" should "return an empty listing that still echoes the request" in {
    val response = MetricsAggregator.topContent(Seq.empty, "files", "30d", limit = 10)
    response.period shouldBe "30d"
    response.contentType shouldBe "files"
    response.items shouldBe empty
  }

  "topContent" should "count unique users per item independently of event count" in {
    val Some(docA) = MetricsAggregator.topContent(threeDocs, "documents", "7d", 10).items.find(_.resourceId == "doc-a"): @unchecked
    docA.eventCount shouldBe 3L
    docA.uniqueUsers shouldBe 3L
  }

  it should "fall back to the resource id when no title metadata is present" in {
    val Some(docB) = MetricsAggregator.topContent(threeDocs, "documents", "7d", 10).items.find(_.resourceId == "doc-b"): @unchecked
    docB.title shouldBe "doc-b"
  }

  it should "restrict to files when asked for files" in {
    val mixed = threeDocs :+ event(EventType.FileUploaded, "u1", "file-a", resourceType = "file")
    val items = MetricsAggregator.topContent(mixed, "files", "7d", 10).items

    items.map(_.resourceId) shouldBe List("file-a")
  }

  it should "include both documents and files for an unknown content type" in {
    val mixed = threeDocs :+ event(EventType.FileUploaded, "u1", "file-a", resourceType = "file")
    val items = MetricsAggregator.topContent(mixed, "everything", "7d", 10).items

    items.map(_.resourceId).toSet shouldBe Set("doc-a", "doc-b", "doc-c", "file-a")
  }

  it should "exclude resource types that are neither document nor file" in {
    val withSession = threeDocs :+ event(EventType.CollabSessionStarted, "u1", "sess-1", resourceType = "session")
    val items = MetricsAggregator.topContent(withSession, "all", "7d", 10).items

    items.map(_.resourceId) should not contain "sess-1"
  }

  it should "cut a tie at the limit without inventing an order (counts are pinned, identity is not)" in {
    // FINDING (documented, not fixed here): items with equal eventCount are ordered by
    // `sortBy(-_.eventCount)` over a HashMap grouping, so which of two tied items survives
    // `take(limit)` is unspecified. Only the counts are asserted here.
    val tied = Seq(
      event(EventType.DocumentViewed, "u1", "doc-x"),
      event(EventType.DocumentViewed, "u1", "doc-y", secondsAgo = 61),
      event(EventType.DocumentViewed, "u1", "doc-z", secondsAgo = 62)
    )

    val items = MetricsAggregator.topContent(tied, "documents", "7d", limit = 2).items

    items should have size 2
    items.map(_.eventCount) shouldBe List(1L, 1L)
  }

  // ---------------------------------------------- user activity recent cap

  private def viewsFor(userId: String, n: Int): Seq[AnalyticsEvent] =
    (1 to n).map(i => event(EventType.DocumentViewed, userId, s"doc-$i", secondsAgo = i.toLong))

  "userActivity with 19 events" should "return all of them in the recent feed" in {
    val activity = MetricsAggregator.userActivity(viewsFor("u1", 19), "u1")
    activity.totalEvents shouldBe 19L
    activity.recentEvents should have size 19
  }

  "userActivity with exactly 20 events" should "return the full feed at the cap" in {
    val activity = MetricsAggregator.userActivity(viewsFor("u1", 20), "u1")
    activity.totalEvents shouldBe 20L
    activity.recentEvents should have size 20
  }

  "userActivity with 21 events" should "truncate the feed to 20 while keeping the true total" in {
    val activity = MetricsAggregator.userActivity(viewsFor("u1", 21), "u1")
    activity.totalEvents shouldBe 21L
    activity.recentEvents should have size 20
  }

  it should "keep the newest events, dropping the oldest" in {
    val activity = MetricsAggregator.userActivity(viewsFor("u1", 21), "u1")
    // secondsAgo == 1 is the newest, secondsAgo == 21 the oldest.
    activity.recentEvents.head.resourceId shouldBe "doc-1"
    activity.recentEvents.map(_.resourceId) should not contain "doc-21"
  }

  "userActivity for a user with no events" should "return zeros and no last-active timestamp" in {
    val activity = MetricsAggregator.userActivity(viewsFor("u1", 3), "nobody")

    activity.userId shouldBe "nobody"
    activity.totalEvents shouldBe 0L
    activity.lastActiveAt shouldBe None
    activity.recentEvents shouldBe empty
  }

  "userActivity" should "not leak another user's events (cross-user negative)" in {
    val events = viewsFor("user-a", 3) ++ viewsFor("user-b", 5)

    val a = MetricsAggregator.userActivity(events, "user-a")
    val b = MetricsAggregator.userActivity(events, "user-b")

    a.totalEvents shouldBe 3L
    b.totalEvents shouldBe 5L
    a.recentEvents.map(_.eventId).toSet intersect b.recentEvents.map(_.eventId).toSet shouldBe empty
  }

  it should "treat a user id differing only by case as a different user" in {
    val events = viewsFor("user-a", 3)
    MetricsAggregator.userActivity(events, "USER-A").totalEvents shouldBe 0L
  }

  // ----------------------------------------------------------- storage usage

  "storageUsage scoped to a user" should "exclude every other user's bytes (cross-user negative)" in {
    val events = Seq(
      event(EventType.StorageAllocated, "user-a", "f1", "file", metadata = Map("bytes" -> "1000")),
      event(EventType.StorageAllocated, "user-b", "f2", "file", secondsAgo = 61, metadata = Map("bytes" -> "9000"))
    )

    MetricsAggregator.storageUsage(events, Some("user-a")).totalStorageBytes shouldBe 1000L
    MetricsAggregator.storageUsage(events, Some("user-b")).totalStorageBytes shouldBe 9000L
    MetricsAggregator.storageUsage(events, None).totalStorageBytes shouldBe 10000L
  }

  "storageUsage for an unknown user" should "report zero rather than the global total" in {
    val events = Seq(event(EventType.StorageAllocated, "user-a", "f1", "file", metadata = Map("bytes" -> "1000")))
    val usage = MetricsAggregator.storageUsage(events, Some("ghost"))

    usage.userId shouldBe Some("ghost")
    usage.totalStorageBytes shouldBe 0L
    usage.breakdownByType shouldBe empty
  }

  "storageUsage when allocations exactly equal releases" should "report zero" in {
    val events = Seq(
      event(EventType.StorageAllocated, "u1", "f1", "file", metadata = Map("bytes" -> "2048")),
      event(EventType.StorageReleased, "u1", "f1", "file", secondsAgo = 61, metadata = Map("bytes" -> "2048"))
    )

    MetricsAggregator.storageUsage(events, None).totalStorageBytes shouldBe 0L
  }

  "storageUsage when releases exceed allocations" should "clamp to zero rather than go negative" in {
    val events = Seq(
      event(EventType.StorageAllocated, "u1", "f1", "file", metadata = Map("bytes" -> "100")),
      event(EventType.StorageReleased, "u1", "f1", "file", secondsAgo = 61, metadata = Map("bytes" -> "500"))
    )

    MetricsAggregator.storageUsage(events, None).totalStorageBytes shouldBe 0L
  }

  "storageUsage breakdown" should "attribute allocated bytes per resource type and ignore releases" in {
    val events = Seq(
      event(EventType.StorageAllocated, "u1", "f1", "file", metadata = Map("bytes" -> "100")),
      event(EventType.StorageAllocated, "u1", "d1", "document", secondsAgo = 61, metadata = Map("bytes" -> "40")),
      event(EventType.StorageReleased, "u1", "f1", "file", secondsAgo = 62, metadata = Map("bytes" -> "30"))
    )

    val usage = MetricsAggregator.storageUsage(events, None)

    usage.breakdownByType shouldBe Map("file" -> 100L, "document" -> 40L)
    usage.totalStorageBytes shouldBe 110L
  }

  it should "skip unparseable byte metadata instead of failing" in {
    val events = Seq(
      event(EventType.StorageAllocated, "u1", "f1", "file", metadata = Map("bytes" -> "twelve")),
      event(EventType.StorageAllocated, "u1", "f2", "file", secondsAgo = 61, metadata = Map("bytes" -> "5"))
    )

    MetricsAggregator.storageUsage(events, None).breakdownByType shouldBe Map("file" -> 5L)
  }

  "storageUsage over no events" should "return an all-zero response" in {
    val usage = MetricsAggregator.storageUsage(Seq.empty, None)

    usage.totalStorageBytes shouldBe 0L
    usage.filesCount shouldBe 0L
    usage.documentsCount shouldBe 0L
    usage.breakdownByType shouldBe empty
  }

  // ------------------------------------------------------------ document stats

  "documentStats for an unknown document" should "return zeros and no timestamps" in {
    val stats = MetricsAggregator.documentStats(Seq.empty, "doc-missing")

    stats.documentId shouldBe "doc-missing"
    stats.views shouldBe 0L
    stats.edits shouldBe 0L
    stats.shares shouldBe 0L
    stats.uniqueViewers shouldBe 0L
    stats.lastViewedAt shouldBe None
    stats.lastEditedAt shouldBe None
  }

  "documentStats" should "count repeat views once per viewer for uniqueViewers" in {
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "doc-1"),
      event(EventType.DocumentViewed, "u1", "doc-1", secondsAgo = 61),
      event(EventType.DocumentViewed, "u2", "doc-1", secondsAgo = 62)
    )

    val stats = MetricsAggregator.documentStats(events, "doc-1")

    stats.views shouldBe 3L
    stats.uniqueViewers shouldBe 2L
  }

  it should "scope strictly to the requested resource id" in {
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "doc-1"),
      event(EventType.DocumentViewed, "u1", "doc-2", secondsAgo = 61)
    )

    MetricsAggregator.documentStats(events, "doc-1").views shouldBe 1L
  }

  it should "report the newest view and edit timestamps" in {
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "doc-1", secondsAgo = 300),
      event(EventType.DocumentViewed, "u1", "doc-1", secondsAgo = 100),
      event(EventType.DocumentEdited, "u1", "doc-1", secondsAgo = 200)
    )

    val stats = MetricsAggregator.documentStats(events, "doc-1")

    stats.lastViewedAt shouldBe Some(Now.minusSeconds(100).toString)
    stats.lastEditedAt shouldBe Some(Now.minusSeconds(200).toString)
  }

  // ----------------------------------------------------------- active users

  "activeUsers over no events" should "report a zero count" in {
    val response = MetricsAggregator.activeUsers(Seq.empty, "daily")
    response.count shouldBe 0L
    response.users shouldBe empty
  }

  "activeUsers" should "rank users by event count, descending" in {
    val events = viewsFor("u1", 3) ++ viewsFor("u2", 1) ++ viewsFor("u3", 2)
    val response = MetricsAggregator.activeUsers(events, "daily")

    response.count shouldBe 3L
    response.users.map(_.userId) shouldBe List("u1", "u3", "u2")
    response.users.map(_.eventCount) shouldBe List(3L, 2L, 1L)
  }

  // ------------------------------------------------------------- period edges

  "periodToCutoff" should "order the supported windows from narrowest to widest" in {
    val daily = MetricsAggregator.periodToCutoff("daily")
    val weekly = MetricsAggregator.periodToCutoff("7d")
    val monthly = MetricsAggregator.periodToCutoff("30d")
    val quarterly = MetricsAggregator.periodToCutoff("90d")

    daily.isAfter(weekly) shouldBe true
    weekly.isAfter(monthly) shouldBe true
    monthly.isAfter(quarterly) shouldBe true
  }

  it should "treat an unknown or empty period as the 7-day default" in {
    val unknown = MetricsAggregator.periodToCutoff("not-a-period")
    val empty = MetricsAggregator.periodToCutoff("")
    val sevenDay = MetricsAggregator.periodToCutoff("7d")

    // All three are derived from separate now() reads; compare with a generous tolerance.
    Math.abs(unknown.getEpochSecond - sevenDay.getEpochSecond) should be <= 5L
    Math.abs(empty.getEpochSecond - sevenDay.getEpochSecond) should be <= 5L
  }

  it should "alias weekly to 7d and monthly to 30d" in {
    val weeklyDelta = Math.abs(
      MetricsAggregator.periodToCutoff("weekly").getEpochSecond - MetricsAggregator.periodToCutoff("7d").getEpochSecond
    )
    val monthlyDelta = Math.abs(
      MetricsAggregator.periodToCutoff("monthly").getEpochSecond - MetricsAggregator.periodToCutoff("30d").getEpochSecond
    )

    weeklyDelta should be <= 5L
    monthlyDelta should be <= 5L
  }

  "dashboardSummary" should "exclude events older than the requested window" in {
    val events = Seq(
      event(EventType.DocumentCreated, "u1", "doc-1", secondsAgo = 60),
      event(EventType.DocumentCreated, "u2", "doc-2", secondsAgo = 40L * 24 * 3600)
    )

    val daily = MetricsAggregator.dashboardSummary(events, "daily")
    val quarterly = MetricsAggregator.dashboardSummary(events, "90d")

    daily.documentsCreated shouldBe 1L
    daily.dailyActiveUsers shouldBe 1L
    quarterly.documentsCreated shouldBe 2L
  }

  it should "return an all-zero summary for no events while echoing the period" in {
    val summary = MetricsAggregator.dashboardSummary(Seq.empty, "30d")

    summary.period shouldBe "30d"
    summary.dailyActiveUsers shouldBe 0L
    summary.totalEvents shouldBe 0L
    summary.storageUsedBytes shouldBe 0L
  }

  it should "count storage across all time even when the event window excludes it" in {
    // storageUsedBytes is deliberately computed over `events`, not the windowed subset.
    val events = Seq(
      event(EventType.StorageAllocated, "u1", "f1", "file", secondsAgo = 40L * 24 * 3600, metadata = Map("bytes" -> "777"))
    )

    val daily = MetricsAggregator.dashboardSummary(events, "daily")

    daily.totalEvents shouldBe 0L
    daily.storageUsedBytes shouldBe 777L
  }

  "exportData" should "return the newest record first and nothing for an empty log" in {
    val events = Seq(
      event(EventType.DocumentViewed, "u1", "doc-old", secondsAgo = 300),
      event(EventType.DocumentViewed, "u1", "doc-new", secondsAgo = 30)
    )

    MetricsAggregator.exportData(events, "7d").map(_("resource_id")) shouldBe List("doc-new", "doc-old")
    MetricsAggregator.exportData(Seq.empty, "7d") shouldBe empty
  }

  it should "emit every documented column for each record" in {
    val Seq(row) = MetricsAggregator.exportData(Seq(event(EventType.DocumentViewed, "u1", "doc-1")), "7d")

    row.keySet shouldBe Set("event_id", "event_type", "user_id", "resource_id", "resource_type", "timestamp")
  }
