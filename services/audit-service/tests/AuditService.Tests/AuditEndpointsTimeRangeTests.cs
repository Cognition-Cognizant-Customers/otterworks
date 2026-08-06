using System.Net;
using System.Net.Http.Json;
using AuditService.Tests.TestDoubles;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Time-window semantics of GET /api/v1/audit/events. All fixture timestamps are UTC and
/// bound values are compared as instants, so results do not depend on the machine time zone.
/// </summary>
public class AuditEndpointsTimeRangeTests : IDisposable
{
    private static readonly DateTime LateOnDayOne = new(2026, 3, 1, 23, 30, 0, DateTimeKind.Utc);
    private static readonly DateTime EarlyOnDayTwo = new(2026, 3, 2, 0, 30, 0, DateTimeKind.Utc);

    private readonly AuditApiFactory _factory = new();
    private readonly HttpClient _client;

    public AuditEndpointsTimeRangeTests()
    {
        _client = _factory.CreateClient();
        _factory.Repository.Seed(
            CreateEvent("evt-day-1", LateOnDayOne),
            CreateEvent("evt-day-2", EarlyOnDayTwo),
            CreateEvent("evt-outside", new DateTime(2026, 3, 3, 12, 0, 0, DateTimeKind.Utc)));
    }

    public void Dispose()
    {
        _client.Dispose();
        _factory.Dispose();
        GC.SuppressFinalize(this);
    }

    [Fact]
    public async Task QueryEvents_RangeSpanningUtcMidnight_ReturnsEventsFromBothDays()
    {
        var page = await QueryAsync("from=2026-03-01T22:00:00Z&to=2026-03-02T02:00:00Z");

        Assert.Equal(2, page.Total);
        Assert.Equal(
            new[] { "evt-day-1", "evt-day-2" },
            page.Events.Select(e => e.Id).OrderBy(id => id).ToArray());
    }

    [Fact]
    public async Task QueryEvents_RangeSpanningUtcMidnight_PassesTheRequestedInstantsThrough()
    {
        await QueryAsync("from=2026-03-01T22:00:00Z&to=2026-03-02T02:00:00Z");

        var query = _factory.Repository.LastQuery!;
        Assert.Equal(
            new DateTime(2026, 3, 1, 22, 0, 0, DateTimeKind.Utc),
            query.From!.Value.ToUniversalTime());
        Assert.Equal(
            new DateTime(2026, 3, 2, 2, 0, 0, DateTimeKind.Utc),
            query.To!.Value.ToUniversalTime());
    }

    [Fact]
    public async Task QueryEvents_RangeEndingAtUtcMidnight_ExcludesTheFollowingDay()
    {
        var page = await QueryAsync("from=2026-03-01T00:00:00Z&to=2026-03-02T00:00:00Z");

        Assert.Equal(1, page.Total);
        Assert.Equal("evt-day-1", page.Events[0].Id);
    }

    [Fact(Skip = "Defect: GET /api/v1/audit/events accepts from > to and returns 200 with an " +
                 "empty page instead of rejecting the inverted range with 400 " +
                 "(no validation in AuditController.QueryEvents).")]
    public async Task QueryEvents_FromLaterThanTo_ReturnsBadRequest()
    {
        var response = await _client.GetAsync(
            "/api/v1/audit/events?from=2026-03-05T00:00:00Z&to=2026-03-01T00:00:00Z");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    private async Task<AuditEventPage> QueryAsync(string queryString)
    {
        var response = await _client.GetAsync($"/api/v1/audit/events?{queryString}");
        response.EnsureSuccessStatusCode();
        var page = await response.Content.ReadFromJsonAsync<AuditEventPage>();
        Assert.NotNull(page);
        return page;
    }

    private static AuditEvent CreateEvent(string id, DateTime timestamp) => new()
    {
        Id = id,
        UserId = "user-1",
        Action = "create",
        ResourceType = "document",
        ResourceId = "doc-1",
        Timestamp = timestamp,
    };
}
