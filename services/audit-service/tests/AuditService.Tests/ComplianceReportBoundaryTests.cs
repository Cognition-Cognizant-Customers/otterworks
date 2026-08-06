using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Moq;
using OtterWorks.AuditService.Config;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Boundary and negative cases for the two generated reports: the compliance report's
/// suspicious-activity threshold (<c>count &gt; max(mean x 3, 100)</c>) and the user activity
/// report's window and recent-event cap.
/// </summary>
public class ComplianceReportBoundaryTests
{
    private readonly Mock<IAuditRepository> _repository = new();
    private readonly OtterWorks.AuditService.Services.AuditService _service;

    public ComplianceReportBoundaryTests()
    {
        _service = new OtterWorks.AuditService.Services.AuditService(
            _repository.Object,
            Mock.Of<IAuditArchiver>(),
            Options.Create(new AwsSettings { ArchiveAfterDays = 90 }),
            Mock.Of<ILogger<OtterWorks.AuditService.Services.AuditService>>());
    }

    private static List<AuditEvent> EventsFor(string userId, int count, DateTime timestamp) =>
        Enumerable.Range(0, count)
            .Select(i => new AuditEvent
            {
                Id = $"{userId}-{i}",
                UserId = userId,
                Action = "read",
                ResourceType = "document",
                ResourceId = $"doc-{i}",
                Timestamp = timestamp,
            })
            .ToList();

    // ---------- suspicious-activity floor of 100 ----------

    [Theory]
    [InlineData(99, false)]  // floor - 1
    [InlineData(100, false)] // exactly the floor: the check is `>`, not `>=`
    [InlineData(101, true)]  // floor + 1
    public async Task SuspiciousActivity_UsesAStrictGreaterThanAgainstTheFloorOf100(int eventCount, bool expectFlagged)
    {
        var timestamp = DateTime.UtcNow.AddDays(-1);
        var events = EventsFor("noisy-user", eventCount, timestamp);
        // 20 quiet users keep the mean low enough that the floor of 100 is the binding threshold.
        for (var i = 0; i < 20; i++)
        {
            events.AddRange(EventsFor($"quiet-{i}", 1, timestamp));
        }

        _repository.Setup(r => r.GetEventsByDateRangeAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>()))
            .ReturnsAsync(events);

        var report = await _service.GetComplianceReportAsync("30d");

        Assert.Equal(expectFlagged, report.SuspiciousActivities.Any(s => s.UserId == "noisy-user"));
        Assert.Equal(21, report.UniqueUsers);
    }

    [Theory]
    [InlineData(385, false)] // threshold (3 x mean) is 385.5 here
    [InlineData(386, true)]
    [InlineData(387, true)]
    public async Task SuspiciousActivity_UsesThreeTimesTheMeanOnceItExceedsTheFloor(int eventCount, bool expectFlagged)
    {
        var timestamp = DateTime.UtcNow.AddDays(-1);
        var events = EventsFor("noisy-user", eventCount, timestamp);
        for (var i = 0; i < 9; i++)
        {
            events.AddRange(EventsFor($"busy-{i}", 100, timestamp));
        }

        _repository.Setup(r => r.GetEventsByDateRangeAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>()))
            .ReturnsAsync(events);

        var report = await _service.GetComplianceReportAsync("30d");

        Assert.Equal(expectFlagged, report.SuspiciousActivities.Any(s => s.UserId == "noisy-user"));
        Assert.DoesNotContain(report.SuspiciousActivities, s => s.UserId.StartsWith("busy-", StringComparison.Ordinal));
    }

    [Fact]
    public async Task SuspiciousActivity_ReportsTheEventCountAndTheThresholdItBreached()
    {
        var timestamp = DateTime.UtcNow.AddDays(-1);
        var events = EventsFor("noisy-user", 150, timestamp);
        for (var i = 0; i < 20; i++)
        {
            events.AddRange(EventsFor($"quiet-{i}", 1, timestamp));
        }

        _repository.Setup(r => r.GetEventsByDateRangeAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>()))
            .ReturnsAsync(events);

        var report = await _service.GetComplianceReportAsync("30d");

        var flagged = Assert.Single(report.SuspiciousActivities);
        Assert.Equal(150, flagged.EventCount);
        Assert.Contains("150", flagged.Reason);
        Assert.Contains("100", flagged.Reason);
    }

    // ---------- empty / degenerate windows ----------

    [Fact]
    public async Task ComplianceReport_OverAnEmptyWindow_IsZeroedAndFlagsNobody()
    {
        _repository.Setup(r => r.GetEventsByDateRangeAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>()))
            .ReturnsAsync(new List<AuditEvent>());

        var report = await _service.GetComplianceReportAsync("30d");

        Assert.Equal(0, report.TotalEvents);
        Assert.Equal(0, report.UniqueUsers);
        Assert.Empty(report.SuspiciousActivities);
        Assert.Empty(report.ActionBreakdown);
        Assert.Empty(report.ResourceTypeBreakdown);
    }

    [Fact]
    public async Task ComplianceReport_WithASingleEvent_FlagsNobody()
    {
        _repository.Setup(r => r.GetEventsByDateRangeAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>()))
            .ReturnsAsync(EventsFor("u-1", 1, DateTime.UtcNow.AddDays(-1)));

        var report = await _service.GetComplianceReportAsync("30d");

        Assert.Equal(1, report.TotalEvents);
        Assert.Empty(report.SuspiciousActivities);
    }

    // ---------- period parsing ----------

    [Theory]
    [InlineData("day", 1)]
    [InlineData("24h", 1)]
    [InlineData("week", 7)]
    [InlineData("7d", 7)]
    [InlineData("month", 30)]
    [InlineData("30d", 30)]
    [InlineData("quarter", 90)]
    [InlineData("90d", 90)]
    [InlineData("year", 365)]
    [InlineData("365d", 365)]
    [InlineData("WEEK", 7)]
    [InlineData("nonsense", 30)]
    [InlineData("", 30)]
    public async Task ReportPeriod_MapsToAWindowOfExactlyThatManyDays(string period, int expectedDays)
    {
        DateTime from = default;
        DateTime to = default;
        _repository.Setup(r => r.GetEventsByDateRangeAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>()))
            .Callback<DateTime, DateTime>((f, t) => (from, to) = (f, t))
            .ReturnsAsync(new List<AuditEvent>());

        await _service.GetComplianceReportAsync(period);

        Assert.Equal(TimeSpan.FromDays(expectedDays), to - from);
    }

    // ---------- user activity report ----------

    [Theory]
    [InlineData(9, 9)]   // cap - 1
    [InlineData(10, 10)] // exactly the cap
    [InlineData(11, 10)] // cap + 1
    public async Task UserActivityReport_CapsRecentEventsAtTen(int eventCount, int expectedRecent)
    {
        var anchor = DateTime.UtcNow.AddDays(-1);
        var events = Enumerable.Range(0, eventCount)
            .Select(i => new AuditEvent
            {
                Id = $"e-{i}",
                UserId = "u-1",
                Action = "read",
                ResourceType = "document",
                ResourceId = $"doc-{i}",
                Timestamp = anchor.AddMinutes(-i),
            })
            .ToList();
        _repository.Setup(r => r.GetAllUserEventsAsync("u-1")).ReturnsAsync(events);

        var report = await _service.GetUserActivityReportAsync("u-1", "30d");

        Assert.Equal(eventCount, report.TotalEvents);
        Assert.Equal(expectedRecent, report.RecentEvents.Count);
        Assert.Equal(anchor, report.RecentEvents[0].Timestamp);
        Assert.Equal(anchor, report.LastActivity);
        Assert.Equal(anchor.AddMinutes(-(eventCount - 1)), report.FirstActivity);
    }

    [Fact]
    public async Task UserActivityReport_ExcludesEventsOutsideTheRequestedWindow()
    {
        var events = new List<AuditEvent>
        {
            new() { Id = "in-window", UserId = "u-1", Action = "read", ResourceType = "doc", ResourceId = "d-1", Timestamp = DateTime.UtcNow.AddDays(-6) },
            new() { Id = "too-old", UserId = "u-1", Action = "read", ResourceType = "doc", ResourceId = "d-2", Timestamp = DateTime.UtcNow.AddDays(-8) },
            new() { Id = "in-the-future", UserId = "u-1", Action = "read", ResourceType = "doc", ResourceId = "d-3", Timestamp = DateTime.UtcNow.AddDays(1) },
        };
        _repository.Setup(r => r.GetAllUserEventsAsync("u-1")).ReturnsAsync(events);

        var report = await _service.GetUserActivityReportAsync("u-1", "7d");

        Assert.Equal(1, report.TotalEvents);
        Assert.Equal("in-window", Assert.Single(report.RecentEvents).Id);
    }

    [Fact]
    public async Task UserActivityReport_ForAUserWithNoEvents_IsEmptyRatherThanNull()
    {
        _repository.Setup(r => r.GetAllUserEventsAsync("ghost")).ReturnsAsync(new List<AuditEvent>());

        var report = await _service.GetUserActivityReportAsync("ghost", "30d");

        Assert.Equal(0, report.TotalEvents);
        Assert.Empty(report.RecentEvents);
        Assert.Empty(report.ActionCounts);
        Assert.Empty(report.ResourceTypeCounts);
        Assert.Null(report.FirstActivity);
        Assert.Null(report.LastActivity);
    }

    [Fact]
    public async Task ResourceHistory_ForAnUnknownResource_IsEmptyRatherThanNull()
    {
        _repository.Setup(r => r.GetResourceHistoryAsync("nope")).ReturnsAsync(new List<AuditEvent>());

        var history = await _service.GetResourceHistoryAsync("nope");

        Assert.Equal("nope", history.ResourceId);
        Assert.Equal(0, history.TotalEvents);
        Assert.Empty(history.Events);
    }
}
