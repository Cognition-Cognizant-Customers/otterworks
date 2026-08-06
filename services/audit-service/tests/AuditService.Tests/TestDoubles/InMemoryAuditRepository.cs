using OtterWorks.AuditService.Services;

namespace AuditService.Tests.TestDoubles;

/// <summary>
/// In-memory stand-in for the DynamoDB-backed repository so API-level tests exercise the
/// real controller + service pipeline without AWS. Paging mirrors the production repository:
/// newest first, then skip/take.
/// </summary>
public sealed class InMemoryAuditRepository : IAuditRepository
{
    private readonly List<AuditEvent> _events = new();

    public QueryCall? LastQuery { get; private set; }

    public IReadOnlyList<AuditEvent> Events => _events;

    public void Seed(params AuditEvent[] events) => _events.AddRange(events);

    public Task SaveEventAsync(AuditEvent auditEvent)
    {
        _events.Add(auditEvent);
        return Task.CompletedTask;
    }

    public Task<AuditEvent?> GetEventAsync(string id)
        => Task.FromResult(_events.FirstOrDefault(e => e.Id == id));

    public Task<AuditEventPage> QueryEventsAsync(
        string? userId, string? action, string? resourceType, string? resourceId,
        DateTime? from, DateTime? to, int page, int pageSize)
    {
        LastQuery = new QueryCall(userId, action, resourceType, resourceId, from, to, page, pageSize);

        var matches = _events
            .Where(e => userId is null || e.UserId == userId)
            .Where(e => action is null || e.Action == action)
            .Where(e => resourceType is null || e.ResourceType == resourceType)
            .Where(e => resourceId is null || e.ResourceId == resourceId)
            .Where(e => !from.HasValue || e.Timestamp >= from.Value.ToUniversalTime())
            .Where(e => !to.HasValue || e.Timestamp <= to.Value.ToUniversalTime())
            .OrderByDescending(e => e.Timestamp)
            .ToList();

        return Task.FromResult(new AuditEventPage
        {
            Events = matches.Skip(Math.Max(page - 1, 0) * pageSize).Take(pageSize).ToList(),
            Total = matches.Count,
            Page = page,
            PageSize = pageSize,
        });
    }

    public Task<List<AuditEvent>> GetAllUserEventsAsync(string userId)
        => Task.FromResult(_events.Where(e => e.UserId == userId).ToList());

    public Task<List<AuditEvent>> GetResourceHistoryAsync(string resourceId)
        => Task.FromResult(_events.Where(e => e.ResourceId == resourceId).ToList());

    public Task<List<AuditEvent>> GetEventsByDateRangeAsync(DateTime from, DateTime to)
        => Task.FromResult(_events.Where(e => e.Timestamp >= from && e.Timestamp <= to).ToList());

    public Task<int> DeleteEventsAsync(IEnumerable<string> eventIds)
    {
        var ids = eventIds.ToHashSet();
        var removed = _events.RemoveAll(e => ids.Contains(e.Id));
        return Task.FromResult(removed);
    }

    public sealed record QueryCall(
        string? UserId,
        string? Action,
        string? ResourceType,
        string? ResourceId,
        DateTime? From,
        DateTime? To,
        int Page,
        int PageSize);
}
