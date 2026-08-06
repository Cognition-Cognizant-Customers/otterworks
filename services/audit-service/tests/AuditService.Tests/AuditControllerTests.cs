using System.Net;
using System.Net.Http.Json;
using System.Text;
using AuditService.Tests.TestSupport;
using Moq;
using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Endpoint-level tests for <c>AuditController.MapAuditEndpoints</c>, driven through an
/// in-memory TestServer with a mocked audit service.
/// </summary>
public class AuditControllerTests
{
    private static readonly DateTime FixedTimestamp = new(2026, 3, 14, 12, 0, 0, DateTimeKind.Utc);

    private static AuditEventRequest ValidRequest() => new()
    {
        UserId = "user-1",
        Action = "create",
        ResourceType = "document",
        ResourceId = "doc-1",
    };

    private static AuditEventResponse ResponseFor(AuditEventRequest request, string id = "evt-1") => new()
    {
        Id = id,
        UserId = request.UserId,
        Action = request.Action,
        ResourceType = request.ResourceType,
        ResourceId = request.ResourceId,
        Timestamp = FixedTimestamp,
    };

    // ---------- positive ----------

    [Fact]
    public async Task RecordEvent_WithValidBody_Returns201WithLocationHeader()
    {
        var request = ValidRequest();
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.RecordEventAsync(It.IsAny<AuditEventRequest>()))
            .ReturnsAsync(ResponseFor(request)));

        var response = await app.Client.PostAsJsonAsync("/api/v1/audit/events", request);

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        Assert.Equal("/api/v1/audit/events/evt-1", response.Headers.Location?.ToString());

        var body = await response.Content.ReadFromJsonAsync<AuditEventResponse>();
        Assert.NotNull(body);
        Assert.Equal("evt-1", body!.Id);
        Assert.Equal("user-1", body.UserId);
        app.AuditService.Verify(s => s.RecordEventAsync(It.Is<AuditEventRequest>(r => r.ResourceId == "doc-1")), Times.Once);
    }

    [Fact]
    public async Task QueryEvents_WithNoQueryString_UsesPageOneAndSizeTwenty()
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.QueryEventsAsync(null, null, null, null, null, null, 1, 20))
            .ReturnsAsync(new AuditEventPage { Page = 1, PageSize = 20 }));

        var response = await app.Client.GetAsync("/api/v1/audit/events");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        app.AuditService.Verify(
            s => s.QueryEventsAsync(null, null, null, null, null, null, 1, 20),
            Times.Once);
    }

    [Fact]
    public async Task QueryEvents_ForwardsEveryFilterToTheService()
    {
        var from = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc);
        var to = new DateTime(2026, 2, 1, 0, 0, 0, DateTimeKind.Utc);
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.QueryEventsAsync(
                "u-1", "delete", "file", "f-9",
                It.IsAny<DateTime?>(), It.IsAny<DateTime?>(), 2, 5))
            .ReturnsAsync(new AuditEventPage { Page = 2, PageSize = 5 }));

        var response = await app.Client.GetAsync(
            "/api/v1/audit/events?user_id=u-1&action=delete&resource_type=file&resource=f-9" +
            $"&from={Uri.EscapeDataString(from.ToString("O"))}&to={Uri.EscapeDataString(to.ToString("O"))}&page=2&size=5");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        app.AuditService.Verify(
            s => s.QueryEventsAsync("u-1", "delete", "file", "f-9", It.IsAny<DateTime?>(), It.IsAny<DateTime?>(), 2, 5),
            Times.Once);
    }

    [Fact]
    public async Task GetEvent_WhenFound_Returns200()
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.GetEventAsync("evt-1"))
            .ReturnsAsync(ResponseFor(ValidRequest())));

        var response = await app.Client.GetAsync("/api/v1/audit/events/evt-1");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<AuditEventResponse>();
        Assert.Equal("evt-1", body!.Id);
    }

    [Fact]
    public async Task GetUserActivityReport_WithoutPeriod_DefaultsTo30d()
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.GetUserActivityReportAsync("u-1", "30d"))
            .ReturnsAsync(new UserActivityReport { UserId = "u-1", Period = "30d" }));

        var response = await app.Client.GetAsync("/api/v1/audit/reports/user/u-1");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        app.AuditService.Verify(s => s.GetUserActivityReportAsync("u-1", "30d"), Times.Once);
    }

    [Fact]
    public async Task GetResourceHistory_Returns200WithHistory()
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.GetResourceHistoryAsync("res-1"))
            .ReturnsAsync(new ResourceHistory { ResourceId = "res-1", TotalEvents = 0 }));

        var response = await app.Client.GetAsync("/api/v1/audit/resources/res-1/history");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<ResourceHistory>();
        Assert.Equal("res-1", body!.ResourceId);
    }

    [Fact]
    public async Task GetComplianceReport_WithoutPeriod_DefaultsTo30d()
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.GetComplianceReportAsync("30d"))
            .ReturnsAsync(new ComplianceReport { Period = "30d" }));

        var response = await app.Client.GetAsync("/api/v1/audit/reports/compliance");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        app.AuditService.Verify(s => s.GetComplianceReportAsync("30d"), Times.Once);
    }

    [Fact]
    public async Task ArchiveOldEvents_Returns200WithArchiveResult()
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.ArchiveOldEventsAsync())
            .ReturnsAsync(new ArchiveResult { ArchivedCount = 3, S3Location = "s3://bucket/key" }));

        var response = await app.Client.PostAsync("/api/v1/audit/archive", content: null);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<ArchiveResult>();
        Assert.Equal(3, body!.ArchivedCount);
    }

    // ---------- negative ----------

    [Theory]
    [InlineData("", "create", "document", "doc-1")]
    [InlineData("user-1", "", "document", "doc-1")]
    [InlineData("user-1", "create", "", "doc-1")]
    [InlineData("user-1", "create", "document", "")]
    [InlineData("   ", "create", "document", "doc-1")]
    [InlineData("user-1", "   ", "document", "doc-1")]
    [InlineData("user-1", "create", "   ", "doc-1")]
    [InlineData("user-1", "create", "document", "   ")]
    public async Task RecordEvent_WithMissingRequiredField_Returns400AndRecordsNothing(
        string userId, string action, string resourceType, string resourceId)
    {
        await using var app = await TestAuditApp.StartAsync();

        var response = await app.Client.PostAsJsonAsync("/api/v1/audit/events", new AuditEventRequest
        {
            UserId = userId,
            Action = action,
            ResourceType = resourceType,
            ResourceId = resourceId,
        });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Contains("required", await response.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
        app.AuditService.Verify(s => s.RecordEventAsync(It.IsAny<AuditEventRequest>()), Times.Never);
    }

    [Fact]
    public async Task RecordEvent_WithMalformedJson_Returns400AndRecordsNothing()
    {
        await using var app = await TestAuditApp.StartAsync();

        var response = await app.Client.PostAsync(
            "/api/v1/audit/events",
            new StringContent("{ \"userId\": ", Encoding.UTF8, "application/json"));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        app.AuditService.Verify(s => s.RecordEventAsync(It.IsAny<AuditEventRequest>()), Times.Never);
    }

    [Fact]
    public async Task RecordEvent_WithEmptyBody_Returns400AndRecordsNothing()
    {
        await using var app = await TestAuditApp.StartAsync();

        var response = await app.Client.PostAsync(
            "/api/v1/audit/events",
            new StringContent(string.Empty, Encoding.UTF8, "application/json"));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        app.AuditService.Verify(s => s.RecordEventAsync(It.IsAny<AuditEventRequest>()), Times.Never);
    }

    [Fact]
    public async Task GetEvent_WhenNotFound_Returns404WithError()
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.GetEventAsync("missing"))
            .ReturnsAsync((AuditEventResponse?)null));

        var response = await app.Client.GetAsync("/api/v1/audit/events/missing");

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.Contains("not found", await response.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
    }

    [Theory]
    [InlineData("xml")]
    [InlineData("pdf")]
    [InlineData("%20")]
    [InlineData("")]
    public async Task ExportAuditLog_WithUnsupportedFormat_Returns400AndExportsNothing(string format)
    {
        await using var app = await TestAuditApp.StartAsync();

        var response = await app.Client.GetAsync($"/api/v1/audit/export?format={format}");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        app.AuditService.Verify(
            s => s.ExportAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>(), It.IsAny<string>()),
            Times.Never);
    }

    [Fact]
    public async Task QueryEvents_WhenServiceThrows_Returns500WithoutLeakingTheException()
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.QueryEventsAsync(
                It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTime?>(), It.IsAny<DateTime?>(), It.IsAny<int>(), It.IsAny<int>()))
            .ThrowsAsync(new InvalidOperationException("dynamo exploded: table otterworks-audit-events")));

        var response = await app.Client.GetAsync("/api/v1/audit/events");
        var body = await response.Content.ReadAsStringAsync();

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        Assert.DoesNotContain("dynamo exploded", body);
        Assert.Contains("internal server error", body, StringComparison.OrdinalIgnoreCase);
    }

    // ---------- boundary ----------

    [Theory]
    [InlineData(0, 1)]     // below the floor -> clamped up
    [InlineData(1, 1)]     // the floor itself
    [InlineData(2, 2)]     // floor + 1
    [InlineData(99, 99)]   // ceiling - 1
    [InlineData(100, 100)] // the ceiling itself
    [InlineData(101, 100)] // ceiling + 1 -> clamped down
    [InlineData(-1, 1)]
    [InlineData(int.MaxValue, 100)]
    public async Task QueryEvents_ClampsPageSizeBetween1And100(int requestedSize, int expectedSize)
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.QueryEventsAsync(
                It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTime?>(), It.IsAny<DateTime?>(), It.IsAny<int>(), It.IsAny<int>()))
            .ReturnsAsync(new AuditEventPage()));

        var response = await app.Client.GetAsync($"/api/v1/audit/events?size={requestedSize}");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        app.AuditService.Verify(
            s => s.QueryEventsAsync(null, null, null, null, null, null, 1, expectedSize),
            Times.Once);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-5)]
    public async Task QueryEvents_DoesNotValidatePageNumber_PassesItThroughUnclamped(int page)
    {
        // Pins current behaviour: only `size` is clamped. See WP-09 findings.
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.QueryEventsAsync(
                It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTime?>(), It.IsAny<DateTime?>(), It.IsAny<int>(), It.IsAny<int>()))
            .ReturnsAsync(new AuditEventPage()));

        var response = await app.Client.GetAsync($"/api/v1/audit/events?page={page}");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        app.AuditService.Verify(
            s => s.QueryEventsAsync(null, null, null, null, null, null, page, 20),
            Times.Once);
    }

    [Theory]
    [InlineData("json")]
    [InlineData("JSON")]
    [InlineData("Json")]
    [InlineData("csv")]
    [InlineData("CSV")]
    [InlineData("cSv")]
    public async Task ExportAuditLog_AcceptsSupportedFormatsCaseInsensitively(string format)
    {
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.ExportAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>(), format))
            .ReturnsAsync(new ExportResult { Format = format }));

        var response = await app.Client.GetAsync($"/api/v1/audit/export?format={format}");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        app.AuditService.Verify(s => s.ExportAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>(), format), Times.Once);
    }

    [Fact]
    public async Task ExportAuditLog_WithNoWindow_DefaultsToTheLast30Days()
    {
        DateTime capturedFrom = default;
        DateTime capturedTo = default;
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.ExportAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>(), "json"))
            .Callback<DateTime, DateTime, string>((f, t, _) => (capturedFrom, capturedTo) = (f, t))
            .ReturnsAsync(new ExportResult()));

        var response = await app.Client.GetAsync("/api/v1/audit/export");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.True(capturedFrom < capturedTo);
        // The default window is exactly 30 days wide; both ends are produced from the same clock read.
        var width = capturedTo - capturedFrom;
        Assert.True(
            Math.Abs((width - TimeSpan.FromDays(30)).TotalSeconds) < 1,
            $"expected a 30-day default export window, got {width}");
    }

    [Fact]
    public async Task ExportAuditLog_WithOnlyFromSupplied_KeepsTheSuppliedStartAndDefaultsTheEnd()
    {
        var from = new DateTime(2020, 1, 1, 0, 0, 0, DateTimeKind.Utc);
        DateTime capturedFrom = default;
        DateTime capturedTo = default;
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.ExportAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>(), "json"))
            .Callback<DateTime, DateTime, string>((f, t, _) => (capturedFrom, capturedTo) = (f, t))
            .ReturnsAsync(new ExportResult()));

        var response = await app.Client.GetAsync(
            $"/api/v1/audit/export?from={Uri.EscapeDataString(from.ToString("O"))}");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(from.Ticks, capturedFrom.ToUniversalTime().Ticks);
        Assert.True(capturedTo > capturedFrom);
    }

    [Fact]
    public async Task ExportAuditLog_WithInvertedWindow_IsAcceptedAndPassedThrough()
    {
        // Pins current behaviour: from > to is not rejected. See WP-09 findings.
        var from = new DateTime(2026, 5, 1, 0, 0, 0, DateTimeKind.Utc);
        var to = new DateTime(2026, 4, 1, 0, 0, 0, DateTimeKind.Utc);
        await using var app = await TestAuditApp.StartAsync(mock => mock
            .Setup(s => s.ExportAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>(), "json"))
            .ReturnsAsync(new ExportResult()));

        var response = await app.Client.GetAsync(
            $"/api/v1/audit/export?from={Uri.EscapeDataString(from.ToString("O"))}&to={Uri.EscapeDataString(to.ToString("O"))}");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        app.AuditService.Verify(
            s => s.ExportAsync(
                It.Is<DateTime>(f => f.ToUniversalTime().Ticks == from.Ticks),
                It.Is<DateTime>(t => t.ToUniversalTime().Ticks == to.Ticks),
                "json"),
            Times.Once);
    }

    [Fact]
    public async Task ExportAuditLog_WithUnparseableDate_Returns400()
    {
        await using var app = await TestAuditApp.StartAsync();

        var response = await app.Client.GetAsync("/api/v1/audit/export?from=not-a-date");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        app.AuditService.Verify(
            s => s.ExportAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>(), It.IsAny<string>()),
            Times.Never);
    }
}
