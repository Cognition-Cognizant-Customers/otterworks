using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using AuditService.Tests.Support;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Authentication / authorization posture of the audit API. Audit data is compliance-relevant, so
/// these cases pin exactly who can reach it today; the <c>Should…</c> cases are skipped and name the
/// gap they will close.
/// </summary>
public class AuditAuthorizationTests
{
    private static readonly string[] ReadRoutes =
    {
        "/api/v1/audit/events",
        "/api/v1/audit/events/e1",
        "/api/v1/audit/reports/user/user-b",
        "/api/v1/audit/resources/doc-1/history",
        "/api/v1/audit/reports/compliance",
        "/api/v1/audit/export",
    };

    public static TheoryData<string> Routes()
    {
        var data = new TheoryData<string>();
        foreach (var route in ReadRoutes)
        {
            data.Add(route);
        }

        return data;
    }

    [Theory]
    [MemberData(nameof(Routes))]
    public async Task EveryReadRoute_IsReachableWithoutAnyCredentials(string route)
    {
        var service = new RecordingAuditService
        {
            StoredEvent = new AuditEvent { Id = "e1", UserId = "user-b" },
        };
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync(route);

        // FINDING (audit-1): no endpoint declares authentication or authorization metadata, so the
        // entire audit trail is readable by any caller that can reach the service.
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task WritingAnAuditEvent_IsReachableWithoutAnyCredentials()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.PostAsJsonAsync("/api/v1/audit/events", new AuditEventRequest
        {
            UserId = "user-b",
            Action = "delete",
            ResourceType = "document",
            ResourceId = "doc-1",
        });

        // FINDING (audit-1): an unauthenticated caller can forge an audit record attributed to any
        // user id, which defeats non-repudiation.
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        Assert.Equal("user-b", Assert.Single(service.RecordCalls).UserId);
    }

    [Fact]
    public async Task NoEndpointCarriesAuthorizationMetadata()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var authorized = app.Endpoints
            .Where(e => e.Metadata.GetMetadata<Microsoft.AspNetCore.Authorization.IAuthorizeData>() is not null)
            .Select(e => e.DisplayName)
            .ToList();

        Assert.Empty(authorized);
    }

    [Fact]
    public async Task NonAdminCallerReadsTheComplianceReport()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/reports/compliance");
        request.Headers.Add("X-User-Id", "user-a");
        request.Headers.Add("X-User-Roles", "USER");
        var response = await app.Client.SendAsync(request);

        // FINDING (audit-1): the role headers the gateway forwards are ignored entirely.
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Single(service.ComplianceCalls);
    }

    [Fact]
    public async Task UserACanReadUserBsActivityReport()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/reports/user/user-b");
        request.Headers.Add("X-User-Id", "user-a");
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", "token-for-user-a");
        var response = await app.Client.SendAsync(request);

        // FINDING (audit-1): cross-tenant read. The path's user id is used verbatim; the caller's
        // identity is never compared with it.
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(("user-b", "30d"), Assert.Single(service.UserReportCalls));
    }

    [Fact]
    public async Task UserACanReadAnEventBelongingToUserB()
    {
        var service = new RecordingAuditService
        {
            StoredEvent = new AuditEvent { Id = "e1", UserId = "user-b", Action = "delete" },
        };
        await using var app = await TestApp.StartAuditApiAsync(service);

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/events/e1");
        request.Headers.Add("X-User-Id", "user-a");
        var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<AuditEventResponse>();
        Assert.Equal("user-b", body!.UserId);
    }

    [Fact]
    public async Task QueryingAnotherUsersEventsIsNotScopedToTheCaller()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/events?user_id=user-b");
        request.Headers.Add("X-User-Id", "user-a");
        await app.Client.SendAsync(request);

        // The caller identity never reaches the service layer, so no owner check is possible there.
        Assert.Equal("user-b", Assert.Single(service.QueryCalls).UserId);
    }

    [Fact(Skip = "FINDING (audit-1): audit-service maps no authentication or authorization. Every route " +
                 "(including POST /events, which forges attribution, and the compliance report) is open to " +
                 "any caller that can reach the pod, and a user can read another user's audit trail. The " +
                 "gateway's X-User-Id / role headers are ignored. Defect fix is a separate PR; do not fix " +
                 "in a coverage PR.")]
    public async Task UnauthenticatedCaller_ShouldBeRejected()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync("/api/v1/audit/events");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact(Skip = "FINDING (audit-1): see above — a non-admin caller should not be able to read the " +
                 "compliance report, and user A should not be able to read user B's audit trail. " +
                 "Defect fix is a separate PR; do not fix in a coverage PR.")]
    public async Task CrossUserRead_ShouldBeForbidden()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/audit/reports/user/user-b");
        request.Headers.Add("X-User-Id", "user-a");
        request.Headers.Add("X-User-Roles", "USER");
        var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }
}
