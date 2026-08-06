using System.Net;
using System.Net.Http.Json;
using AuditService.Tests.TestDoubles;
using OtterWorks.AuditService.Models;

namespace AuditService.Tests;

/// <summary>
/// The audit trail is the compliance surface: once recorded, an event must not be
/// mutable or replaceable through the public API.
/// </summary>
public class AuditEndpointsImmutabilityTests : IDisposable
{
    private readonly AuditApiFactory _factory = new();
    private readonly HttpClient _client;

    public AuditEndpointsImmutabilityTests()
    {
        _client = _factory.CreateClient();
    }

    public void Dispose()
    {
        _client.Dispose();
        _factory.Dispose();
        GC.SuppressFinalize(this);
    }

    [Theory]
    [InlineData("PUT")]
    [InlineData("PATCH")]
    [InlineData("DELETE")]
    public async Task ExistingEvent_MutatingHttpMethod_IsNotAccepted(string method)
    {
        var recorded = await RecordEventAsync("create");

        using var request = new HttpRequestMessage(
            new HttpMethod(method), $"/api/v1/audit/events/{recorded.Id}")
        {
            Content = JsonContent.Create(new { action = "tampered" }),
        };
        var response = await _client.SendAsync(request);

        Assert.True(
            response.StatusCode is HttpStatusCode.MethodNotAllowed or HttpStatusCode.NotFound,
            $"{method} on an audit event returned {(int)response.StatusCode}; mutation must not be exposed.");

        var reread = await _client.GetFromJsonAsync<AuditEventResponse>(
            $"/api/v1/audit/events/{recorded.Id}");
        Assert.Equal("create", reread!.Action);
    }

    [Fact]
    public async Task ExistingEvent_RecordingAgainWithSamePayload_LeavesTheOriginalIntact()
    {
        var first = await RecordEventAsync("create");
        var second = await RecordEventAsync("create");

        Assert.NotEqual(first.Id, second.Id);

        var reread = await _client.GetFromJsonAsync<AuditEventResponse>(
            $"/api/v1/audit/events/{first.Id}");
        Assert.Equal(first.Id, reread!.Id);
        Assert.Equal(first.Action, reread.Action);
        Assert.Equal(first.UserId, reread.UserId);
        Assert.Equal(first.Timestamp, reread.Timestamp);
    }

    [Fact]
    public async Task ExistingEvent_PostingToItsOwnUrl_IsNotAccepted()
    {
        var recorded = await RecordEventAsync("create");

        var response = await _client.PostAsJsonAsync(
            $"/api/v1/audit/events/{recorded.Id}",
            new AuditEventRequest
            {
                UserId = "attacker",
                Action = "overwrite",
                ResourceType = "document",
                ResourceId = "doc-1",
            });

        Assert.True(
            response.StatusCode is HttpStatusCode.MethodNotAllowed or HttpStatusCode.NotFound,
            $"POST to an event URL returned {(int)response.StatusCode}; overwrite must not be exposed.");
    }

    [Fact]
    public async Task GetEvent_IdDoesNotExist_ReturnsNotFound()
    {
        var response = await _client.GetAsync("/api/v1/audit/events/does-not-exist");

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task RecordEvent_RequiredFieldMissing_ReturnsBadRequest()
    {
        var response = await _client.PostAsJsonAsync(
            "/api/v1/audit/events",
            new AuditEventRequest
            {
                UserId = "user-1",
                Action = string.Empty,
                ResourceType = "document",
                ResourceId = "doc-1",
            });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    private async Task<AuditEventResponse> RecordEventAsync(string action)
    {
        var response = await _client.PostAsJsonAsync(
            "/api/v1/audit/events",
            new AuditEventRequest
            {
                UserId = "user-1",
                Action = action,
                ResourceType = "document",
                ResourceId = "doc-1",
            });

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var recorded = await response.Content.ReadFromJsonAsync<AuditEventResponse>();
        Assert.NotNull(recorded);
        return recorded;
    }
}
