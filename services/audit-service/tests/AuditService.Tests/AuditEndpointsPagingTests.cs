using System.Net;
using System.Net.Http.Json;
using AuditService.Tests.TestDoubles;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Query paging behaviour of GET /api/v1/audit/events, driven through the HTTP surface.
/// </summary>
public class AuditEndpointsPagingTests : IDisposable
{
    private readonly AuditApiFactory _factory = new();
    private readonly HttpClient _client;

    public AuditEndpointsPagingTests()
    {
        _client = _factory.CreateClient();
    }

    public void Dispose()
    {
        _client.Dispose();
        _factory.Dispose();
        GC.SuppressFinalize(this);
    }

    [Fact]
    public async Task QueryEvents_SizeAbsent_UsesDefaultPageSizeOf20()
    {
        var page = await QueryAsync("?user_id=user-1");

        Assert.Equal(20, page.PageSize);
        Assert.Equal(20, _factory.Repository.LastQuery!.PageSize);
    }

    [Theory]
    [InlineData(0, 1)]
    [InlineData(1, 1)]
    [InlineData(100, 100)]
    [InlineData(101, 100)]
    [InlineData(-5, 1)]
    public async Task QueryEvents_SizeOutsideAllowedRange_IsClampedToOneHundredMax(
        int requestedSize, int expectedSize)
    {
        var page = await QueryAsync($"?size={requestedSize}");

        Assert.Equal(expectedSize, page.PageSize);
        Assert.Equal(expectedSize, _factory.Repository.LastQuery!.PageSize);
    }

    [Fact]
    public async Task QueryEvents_SizeAtLowerBound_ReturnsASingleEvent()
    {
        SeedEvents(3);

        var page = await QueryAsync("?size=1");

        Assert.Single(page.Events);
        Assert.Equal(3, page.Total);
    }

    [Fact]
    public async Task QueryEvents_FirstPage_ReturnsTheFirstSlice()
    {
        SeedEvents(5);

        var page = await QueryAsync("?page=1&size=2");

        Assert.Equal(1, page.Page);
        Assert.Equal(2, page.Events.Count);
        Assert.Equal(5, page.Total);
    }

    [Fact]
    public async Task QueryEvents_LastPartialPage_ReturnsRemainingEvents()
    {
        SeedEvents(5);

        var page = await QueryAsync("?page=3&size=2");

        Assert.Equal(3, page.Page);
        Assert.Single(page.Events);
        Assert.Equal(5, page.Total);
    }

    [Fact]
    public async Task QueryEvents_PagePastTheEnd_ReturnsOkWithEmptyCollectionAndFullTotal()
    {
        SeedEvents(3);

        var response = await _client.GetAsync("/api/v1/audit/events?page=99&size=20");
        var page = await ReadPageAsync(response);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Empty(page.Events);
        Assert.Equal(3, page.Total);
        Assert.Equal(99, page.Page);
    }

    [Fact]
    public async Task QueryEvents_NoMatchingEvents_ReturnsOkWithEmptyCollection()
    {
        SeedEvents(3);

        var response = await _client.GetAsync("/api/v1/audit/events?user_id=nobody");
        var page = await ReadPageAsync(response);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Empty(page.Events);
        Assert.Equal(0, page.Total);
    }

    private void SeedEvents(int count)
    {
        var baseTime = new DateTime(2026, 3, 1, 12, 0, 0, DateTimeKind.Utc);
        _factory.Repository.Seed(Enumerable.Range(0, count).Select(i => new AuditEvent
        {
            Id = $"evt-{i}",
            UserId = "user-1",
            Action = "create",
            ResourceType = "document",
            ResourceId = $"doc-{i}",
            Timestamp = baseTime.AddMinutes(i),
        }).ToArray());
    }

    private async Task<AuditEventPage> QueryAsync(string queryString)
    {
        var response = await _client.GetAsync($"/api/v1/audit/events{queryString}");
        return await ReadPageAsync(response);
    }

    private static async Task<AuditEventPage> ReadPageAsync(HttpResponseMessage response)
    {
        response.EnsureSuccessStatusCode();
        var page = await response.Content.ReadFromJsonAsync<AuditEventPage>();
        Assert.NotNull(page);
        return page;
    }
}
