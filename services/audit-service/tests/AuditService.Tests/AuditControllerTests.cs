using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using AuditService.Tests.Support;
using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Every action of <see cref="OtterWorks.AuditService.Controllers.AuditController"/>: happy path,
/// input validation, pagination bounds and what the controller forwards to the service layer.
/// </summary>
public class AuditControllerTests
{
    private static readonly AuditEventRequest ValidRequest = new()
    {
        UserId = "user-a",
        Action = "create",
        ResourceType = "document",
        ResourceId = "doc-1",
    };

    private static StringContent Json(string raw) => new(raw, Encoding.UTF8, "application/json");

    // ------------------------------------------------------- POST /events

    [Fact]
    public async Task RecordEvent_ReturnsCreatedWithLocationAndBody()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.PostAsJsonAsync("/api/v1/audit/events", ValidRequest);

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        Assert.Equal("/api/v1/audit/events/evt-001", response.Headers.Location?.ToString());

        var body = await response.Content.ReadFromJsonAsync<AuditEventResponse>();
        Assert.NotNull(body);
        Assert.Equal("user-a", body!.UserId);
        Assert.Equal("create", body.Action);
        Assert.Equal(ValidRequest.ResourceId, Assert.Single(service.RecordCalls).ResourceId);
    }

    [Fact]
    public async Task RecordEvent_ForwardsOptionalMetadata()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.PostAsJsonAsync("/api/v1/audit/events", new AuditEventRequest
        {
            UserId = "user-a",
            Action = "share",
            ResourceType = "file",
            ResourceId = "file-1",
            IpAddress = "203.0.113.9",
            UserAgent = "otter/2.0",
            Details = new Dictionary<string, string> { ["sharedWith"] = "user-b" },
        });

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var recorded = Assert.Single(service.RecordCalls);
        Assert.Equal("203.0.113.9", recorded.IpAddress);
        Assert.Equal("otter/2.0", recorded.UserAgent);
        Assert.Equal("user-b", Assert.Contains("sharedWith", recorded.Details!));
    }

    [Theory]
    [InlineData("""{"action":"create","resourceType":"document","resourceId":"doc-1"}""")]
    [InlineData("""{"userId":"user-a","resourceType":"document","resourceId":"doc-1"}""")]
    [InlineData("""{"userId":"user-a","action":"create","resourceId":"doc-1"}""")]
    [InlineData("""{"userId":"user-a","action":"create","resourceType":"document"}""")]
    [InlineData("""{"userId":"  ","action":"create","resourceType":"document","resourceId":"doc-1"}""")]
    [InlineData("""{"userId":"user-a","action":"","resourceType":"document","resourceId":"doc-1"}""")]
    public async Task RecordEvent_RejectsMissingOrBlankRequiredFields(string payload)
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.PostAsync("/api/v1/audit/events", Json(payload));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty(service.RecordCalls);
    }

    [Fact]
    public async Task RecordEvent_RejectsMalformedJson()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.PostAsync("/api/v1/audit/events", Json("{ not json"));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty(service.RecordCalls);
    }

    [Fact]
    public async Task RecordEvent_RejectsAnEmptyBody()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.PostAsync("/api/v1/audit/events", Json(string.Empty));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty(service.RecordCalls);
    }

    [Fact]
    public async Task RecordEvent_RejectsAnUnsupportedContentType()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.PostAsync(
            "/api/v1/audit/events", new StringContent("userId=user-a", Encoding.UTF8, "text/plain"));

        Assert.Equal(HttpStatusCode.UnsupportedMediaType, response.StatusCode);
        Assert.Empty(service.RecordCalls);
    }

    // -------------------------------------------------------- GET /events

    [Fact]
    public async Task QueryEvents_AppliesDefaultPagination()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync("/api/v1/audit/events");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var call = Assert.Single(service.QueryCalls);
        Assert.Equal(1, call.Page);
        Assert.Equal(20, call.PageSize);
        Assert.Null(call.UserId);
        Assert.Null(call.From);
        Assert.Null(call.To);
    }

    [Fact]
    public async Task QueryEvents_ForwardsEveryFilter()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync(
            "/api/v1/audit/events?user_id=user-a&action=delete&resource_type=document&resource=doc-1" +
            "&from=2026-01-01T00:00:00Z&to=2026-01-31T00:00:00Z&page=2&size=50");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var call = Assert.Single(service.QueryCalls);
        Assert.Equal("user-a", call.UserId);
        Assert.Equal("delete", call.Action);
        Assert.Equal("document", call.ResourceType);
        Assert.Equal("doc-1", call.ResourceId);
        Assert.Equal(2, call.Page);
        Assert.Equal(50, call.PageSize);
        Assert.Equal(new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc), call.From!.Value.ToUniversalTime());
        Assert.Equal(new DateTime(2026, 1, 31, 0, 0, 0, DateTimeKind.Utc), call.To!.Value.ToUniversalTime());
    }

    [Theory]
    [InlineData(0, 1)]      // below the floor -> clamped up
    [InlineData(1, 1)]      // exactly the floor
    [InlineData(2, 2)]      // just above the floor
    [InlineData(99, 99)]    // max - 1
    [InlineData(100, 100)]  // max
    [InlineData(101, 100)]  // max + 1 -> clamped down
    [InlineData(-1, 1)]     // negative -> clamped up
    [InlineData(int.MaxValue, 100)]
    public async Task QueryEvents_ClampsPageSizeToTheOneToHundredWindow(int requested, int expected)
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync($"/api/v1/audit/events?size={requested}");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(expected, Assert.Single(service.QueryCalls).PageSize);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public async Task QueryEvents_ForwardsOutOfRangePageNumbersUnchanged(int page)
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync($"/api/v1/audit/events?page={page}");

        // FINDING (audit-5): page is not validated or clamped the way size is; page=0/-1 reach the
        // repository, which computes a negative Skip() and silently returns the first page while
        // echoing the bogus page number back to the caller.
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(page, Assert.Single(service.QueryCalls).Page);
    }

    [Theory(Skip = "FINDING (audit-5): AuditController.QueryEvents clamps size with Math.Clamp but leaves " +
                   "page unvalidated, so page=0 and page=-1 are accepted and forwarded. A non-positive page " +
                   "should be a 400. Defect fix is a separate PR; do not fix in a coverage PR.")]
    [InlineData(0)]
    [InlineData(-1)]
    public async Task QueryEvents_ShouldRejectNonPositivePageNumbers(int page)
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync($"/api/v1/audit/events?page={page}");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Theory]
    [InlineData("page=abc")]
    [InlineData("size=1.5")]
    [InlineData("from=not-a-date")]
    [InlineData("to=2026-13-45")]
    public async Task QueryEvents_RejectsMalformedFilterValues(string query)
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync($"/api/v1/audit/events?{query}");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty(service.QueryCalls);
    }

    [Fact]
    public async Task QueryEvents_ReturnsThePageEnvelopeFromTheService()
    {
        var service = new RecordingAuditService
        {
            PageResult = new AuditEventPage
            {
                Events = new List<AuditEvent> { new() { Id = "e1", UserId = "user-a" } },
                Total = 1,
                Page = 1,
                PageSize = 20,
            },
        };
        await using var app = await TestApp.StartAuditApiAsync(service);

        var page = await app.Client.GetFromJsonAsync<AuditEventPage>("/api/v1/audit/events");

        Assert.NotNull(page);
        Assert.Equal(1, page!.Total);
        Assert.Equal("e1", Assert.Single(page.Events).Id);
    }

    // --------------------------------------------------- GET /events/{id}

    [Fact]
    public async Task GetEvent_ReturnsTheEventWhenPresent()
    {
        var service = new RecordingAuditService
        {
            StoredEvent = new AuditEvent { Id = "e1", UserId = "user-a", Action = "read" },
        };
        await using var app = await TestApp.StartAuditApiAsync(service);

        var body = await app.Client.GetFromJsonAsync<AuditEventResponse>("/api/v1/audit/events/e1");

        Assert.Equal("e1", body!.Id);
        Assert.Equal("e1", Assert.Single(service.GetEventCalls));
    }

    [Fact]
    public async Task GetEvent_Returns404ForAnUnknownId()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync("/api/v1/audit/events/does-not-exist");

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        Assert.Equal("Event not found.", body.RootElement.GetProperty("error").GetString());
    }

    [Fact]
    public async Task GetEvent_DecodesAnEscapedIdBeforeLookup()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync("/api/v1/audit/events/a%20b");

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.Equal("a b", Assert.Single(service.GetEventCalls));
    }

    // ---------------------------------------------------------- reports

    [Fact]
    public async Task UserActivityReport_DefaultsToThirtyDays()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync("/api/v1/audit/reports/user/user-a");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(("user-a", "30d"), Assert.Single(service.UserReportCalls));
    }

    [Theory]
    [InlineData("24h")]
    [InlineData("7d")]
    [InlineData("quarter")]
    [InlineData("not-a-period")]
    public async Task UserActivityReport_ForwardsThePeriodVerbatim(string period)
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync($"/api/v1/audit/reports/user/user-a?period={period}");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(("user-a", period), Assert.Single(service.UserReportCalls));
    }

    [Fact]
    public async Task ResourceHistory_ForwardsTheResourceId()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var body = await app.Client.GetFromJsonAsync<ResourceHistory>("/api/v1/audit/resources/doc-1/history");

        Assert.Equal("doc-1", body!.ResourceId);
        Assert.Equal("doc-1", Assert.Single(service.ResourceHistoryCalls));
    }

    [Fact]
    public async Task ComplianceReport_DefaultsToThirtyDays()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var body = await app.Client.GetFromJsonAsync<ComplianceReport>("/api/v1/audit/reports/compliance");

        Assert.Equal("30d", body!.Period);
        Assert.Equal("30d", Assert.Single(service.ComplianceCalls));
    }

    [Fact]
    public async Task ComplianceReport_ForwardsAnExplicitPeriod()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        await app.Client.GetAsync("/api/v1/audit/reports/compliance?period=year");

        Assert.Equal("year", Assert.Single(service.ComplianceCalls));
    }

    // ----------------------------------------------------------- archive

    [Fact]
    public async Task Archive_InvokesTheServiceAndReturnsTheResult()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.PostAsync("/api/v1/audit/archive", content: null);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(1, service.ArchiveCalls);
    }

    // -------------------------------------------------------- misc routing

    [Fact]
    public async Task UnknownAuditRoute_Returns404()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync("/api/v1/audit/unknown");

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task ServiceFailure_SurfacesRatherThanBeingSwallowed()
    {
        var service = new RecordingAuditService { Failure = new InvalidOperationException("dynamo down") };
        await using var app = await TestApp.StartAuditApiAsync(service);

        // No error-handling middleware is mapped here, so the exception must reach the host —
        // proving the controller does not swallow repository failures into a 200.
        await Assert.ThrowsAsync<InvalidOperationException>(
            () => app.Client.GetAsync("/api/v1/audit/events"));
    }
}
