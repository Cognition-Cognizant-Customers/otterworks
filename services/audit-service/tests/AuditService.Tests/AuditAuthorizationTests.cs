using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using AuditService.Tests.TestSupport;
using Moq;
using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Authorization negatives for every audit route.
///
/// WP-09 finding (genuine gap, not a planted bug): audit-service performs **no**
/// authentication or authorization of its own. There is no auth middleware in Program.cs and
/// no endpoint carries <c>RequireAuthorization</c>, so every route — including the
/// compliance report, the full export and the archive trigger — is served to an anonymous or
/// non-admin caller that can reach the pod. Enforcement exists only at the api-gateway.
/// The tests below pin that behaviour so a fix flips them deliberately; the intended
/// behaviour is expressed by the skipped tests at the bottom of this file.
/// </summary>
public class AuditAuthorizationTests
{
    public static TheoryData<string, string> AllRoutes() => new()
    {
        { "GET", "/api/v1/audit/events" },
        { "GET", "/api/v1/audit/events/evt-1" },
        { "GET", "/api/v1/audit/reports/user/victim-user" },
        { "GET", "/api/v1/audit/resources/res-1/history" },
        { "GET", "/api/v1/audit/reports/compliance" },
        { "GET", "/api/v1/audit/export" },
        { "POST", "/api/v1/audit/events" },
        { "POST", "/api/v1/audit/archive" },
    };

    private static async Task<TestAuditApp> StartWithStubbedServiceAsync()
    {
        return await TestAuditApp.StartAsync(mock =>
        {
            mock.Setup(s => s.RecordEventAsync(It.IsAny<AuditEventRequest>()))
                .ReturnsAsync(new AuditEventResponse { Id = "evt-1" });
            mock.Setup(s => s.GetEventAsync(It.IsAny<string>()))
                .ReturnsAsync(new AuditEventResponse { Id = "evt-1" });
            mock.Setup(s => s.QueryEventsAsync(
                    It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(),
                    It.IsAny<DateTime?>(), It.IsAny<DateTime?>(), It.IsAny<int>(), It.IsAny<int>()))
                .ReturnsAsync(new AuditEventPage());
            mock.Setup(s => s.GetUserActivityReportAsync(It.IsAny<string>(), It.IsAny<string>()))
                .ReturnsAsync(new UserActivityReport { UserId = "victim-user" });
            mock.Setup(s => s.GetResourceHistoryAsync(It.IsAny<string>()))
                .ReturnsAsync(new ResourceHistory { ResourceId = "res-1" });
            mock.Setup(s => s.GetComplianceReportAsync(It.IsAny<string>()))
                .ReturnsAsync(new ComplianceReport());
            mock.Setup(s => s.ExportAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>(), It.IsAny<string>()))
                .ReturnsAsync(new ExportResult());
            mock.Setup(s => s.ArchiveOldEventsAsync())
                .ReturnsAsync(new ArchiveResult());
        });
    }

    private static HttpRequestMessage BuildRequest(string method, string path)
    {
        var request = new HttpRequestMessage(new HttpMethod(method), path);
        if (method == "POST" && path.EndsWith("/events", StringComparison.Ordinal))
        {
            request.Content = JsonContent.Create(new AuditEventRequest
            {
                UserId = "attacker",
                Action = "create",
                ResourceType = "document",
                ResourceId = "doc-1",
            });
        }

        return request;
    }

    [Theory]
    [MemberData(nameof(AllRoutes))]
    public async Task Route_WithNoCredentialsAtAll_IsCurrentlyServed_NoServiceLevelAuthz(string method, string path)
    {
        await using var app = await StartWithStubbedServiceAsync();

        var response = await app.Client.SendAsync(BuildRequest(method, path));

        Assert.NotEqual(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.NotEqual(HttpStatusCode.Forbidden, response.StatusCode);
        Assert.True(response.IsSuccessStatusCode, $"{method} {path} returned {(int)response.StatusCode}");
    }

    [Theory]
    [MemberData(nameof(AllRoutes))]
    public async Task Route_WithNonAdminCaller_IsCurrentlyServed_NoServiceLevelAuthz(string method, string path)
    {
        await using var app = await StartWithStubbedServiceAsync();

        var request = BuildRequest(method, path);
        request.Headers.Add("X-User-Id", "attacker");
        request.Headers.Add("X-User-Roles", "USER");

        var response = await app.Client.SendAsync(request);

        Assert.NotEqual(HttpStatusCode.Forbidden, response.StatusCode);
        Assert.True(response.IsSuccessStatusCode, $"{method} {path} returned {(int)response.StatusCode}");
    }

    [Theory]
    [MemberData(nameof(AllRoutes))]
    public async Task Route_WithGarbageBearerToken_IsCurrentlyServed_TokenIsNeverValidated(string method, string path)
    {
        await using var app = await StartWithStubbedServiceAsync();

        var request = BuildRequest(method, path);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", "not.a.jwt");

        var response = await app.Client.SendAsync(request);

        Assert.NotEqual(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.True(response.IsSuccessStatusCode, $"{method} {path} returned {(int)response.StatusCode}");
    }

    [Fact]
    public async Task UserActivityReport_ForAnotherUser_IsCurrentlyServed_NoOwnershipCheck()
    {
        await using var app = await StartWithStubbedServiceAsync();

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/reports/user/victim-user");
        request.Headers.Add("X-User-Id", "attacker");

        var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var report = await response.Content.ReadFromJsonAsync<UserActivityReport>();
        Assert.Equal("victim-user", report!.UserId);
        app.AuditService.Verify(s => s.GetUserActivityReportAsync("victim-user", "30d"), Times.Once);
    }

    [Fact]
    public async Task RecordEvent_LetsTheCallerAttributeTheEventToAnyUserId_NoIdentityBinding()
    {
        // The recorded actor comes from the request body, never from an authenticated principal,
        // so any caller can forge an audit entry on behalf of another user.
        await using var app = await StartWithStubbedServiceAsync();

        var request = new HttpRequestMessage(HttpMethod.Post, "/api/v1/audit/events")
        {
            Content = JsonContent.Create(new AuditEventRequest
            {
                UserId = "someone-else",
                Action = "delete",
                ResourceType = "document",
                ResourceId = "doc-1",
            }),
        };
        request.Headers.Add("X-User-Id", "attacker");

        var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        app.AuditService.Verify(
            s => s.RecordEventAsync(It.Is<AuditEventRequest>(r => r.UserId == "someone-else")),
            Times.Once);
    }

    [Theory(Skip = "WP-09 finding: audit-service enforces no authorization; admin-only routes are open. Documented, not fixed (test-only work package).")]
    [MemberData(nameof(AllRoutes))]
    public async Task Route_WithNonAdminCaller_ShouldReturn403(string method, string path)
    {
        await using var app = await StartWithStubbedServiceAsync();

        var request = BuildRequest(method, path);
        request.Headers.Add("X-User-Id", "attacker");
        request.Headers.Add("X-User-Roles", "USER");

        var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    [Fact(Skip = "WP-09 finding: audit-service enforces no authentication; anonymous callers are served. Documented, not fixed (test-only work package).")]
    public async Task ComplianceReport_WithoutCredentials_ShouldReturn401()
    {
        await using var app = await StartWithStubbedServiceAsync();

        var response = await app.Client.GetAsync("/api/v1/audit/reports/compliance");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }
}
