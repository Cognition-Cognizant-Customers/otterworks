using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Moq;
using OtterWorks.AuditService.Config;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Suspicious-activity detection in the compliance report. The threshold is the greater of
/// three times the average per-user event count and a floor of 100 events.
/// </summary>
public class AuditServiceComplianceThresholdTests
{
    private readonly Mock<IAuditRepository> _mockRepository = new();
    private readonly OtterWorks.AuditService.Services.AuditService _service;

    public AuditServiceComplianceThresholdTests()
    {
        _service = new OtterWorks.AuditService.Services.AuditService(
            _mockRepository.Object,
            new Mock<IAuditArchiver>().Object,
            Options.Create(new AwsSettings { Region = "us-east-1", DynamoDbTable = "test-table" }),
            new Mock<ILogger<OtterWorks.AuditService.Services.AuditService>>().Object);
    }

    [Theory]
    [InlineData(99, false)]
    [InlineData(100, false)]
    [InlineData(101, true)]
    public async Task GetComplianceReportAsync_EventCountAroundTheFloorOf100_FlagsOnlyAboveIt(
        int eventCount, bool expectedSuspicious)
    {
        // Twenty single-event users keep three times the average well below the floor of 100,
        // so the floor is the effective threshold.
        var events = EventsFor("heavy-user", eventCount);
        for (var i = 0; i < 20; i++)
            events.AddRange(EventsFor($"quiet-user-{i}", 1));

        SetupEvents(events);

        var report = await _service.GetComplianceReportAsync("30d");

        Assert.Equal(expectedSuspicious, report.SuspiciousActivities.Any(s => s.UserId == "heavy-user"));
    }

    [Fact]
    public async Task GetComplianceReportAsync_EventCountAtOneAboveTheFloor_ReportsTheEventCount()
    {
        var events = EventsFor("heavy-user", 101);
        for (var i = 0; i < 20; i++)
            events.AddRange(EventsFor($"quiet-user-{i}", 1));

        SetupEvents(events);

        var report = await _service.GetComplianceReportAsync("30d");

        var flagged = Assert.Single(report.SuspiciousActivities);
        Assert.Equal("heavy-user", flagged.UserId);
        Assert.Equal(101, flagged.EventCount);
    }

    [Fact]
    public async Task GetComplianceReportAsync_ThreeTimesAverageAboveTheFloor_UsesTheAverageAsThreshold()
    {
        // Average is ~100.8 events/user, so the threshold is ~302 rather than the floor of 100:
        // the 200-event user stays below it while the 1000-event user is flagged.
        var events = EventsFor("outlier-user", 1000);
        events.AddRange(EventsFor("busy-user", 200));
        for (var i = 0; i < 10; i++)
            events.AddRange(EventsFor($"quiet-user-{i}", 1));

        SetupEvents(events);

        var report = await _service.GetComplianceReportAsync("30d");

        var flagged = Assert.Single(report.SuspiciousActivities);
        Assert.Equal("outlier-user", flagged.UserId);
    }

    [Fact]
    public async Task GetComplianceReportAsync_NoEvents_ReturnsEmptyReportWithoutDividingByZero()
    {
        SetupEvents(new List<AuditEvent>());

        var report = await _service.GetComplianceReportAsync("30d");

        Assert.Equal(0, report.TotalEvents);
        Assert.Equal(0, report.UniqueUsers);
        Assert.Empty(report.SuspiciousActivities);
        Assert.Empty(report.ActionBreakdown);
        Assert.Empty(report.ResourceTypeBreakdown);
    }

    [Fact]
    public async Task GetComplianceReportAsync_SingleUserBelowTheFloor_FlagsNobody()
    {
        SetupEvents(EventsFor("only-user", 5));

        var report = await _service.GetComplianceReportAsync("30d");

        Assert.Equal(5, report.TotalEvents);
        Assert.Equal(1, report.UniqueUsers);
        Assert.Empty(report.SuspiciousActivities);
    }

    private void SetupEvents(List<AuditEvent> events)
    {
        _mockRepository
            .Setup(r => r.GetEventsByDateRangeAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>()))
            .ReturnsAsync(events);
    }

    private static List<AuditEvent> EventsFor(string userId, int count)
    {
        var baseTime = new DateTime(2026, 3, 1, 0, 0, 0, DateTimeKind.Utc);
        return Enumerable.Range(0, count).Select(i => new AuditEvent
        {
            Id = $"{userId}-{i}",
            UserId = userId,
            Action = "read",
            ResourceType = "document",
            ResourceId = $"doc-{i}",
            Timestamp = baseTime.AddSeconds(i),
        }).ToList();
    }
}
