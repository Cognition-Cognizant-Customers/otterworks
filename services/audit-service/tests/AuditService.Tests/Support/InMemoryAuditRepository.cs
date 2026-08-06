using OtterWorks.AuditService.Services;

namespace AuditService.Tests.Support;

/// <summary>
/// In-memory <see cref="IAuditRepository"/> keyed by event id, mirroring DynamoDB
/// <c>PutItem</c> semantics (a save for an existing id replaces the stored item, so the row
/// count is the number of distinct ids that were written).
/// </summary>
public sealed class InMemoryAuditRepository : IAuditRepository
{
    private readonly Dictionary<string, AuditEvent> _rows = new(StringComparer.Ordinal);
    private readonly object _gate = new();

    public int SaveCallCount { get; private set; }

    public Exception? SaveFailure { get; set; }

    public IReadOnlyList<AuditEvent> Rows
    {
        get
        {
            lock (_gate)
            {
                return _rows.Values.OrderBy(e => e.Id, StringComparer.Ordinal).ToList();
            }
        }
    }

    public int RowCount
    {
        get
        {
            lock (_gate)
            {
                return _rows.Count;
            }
        }
    }

    public void Seed(params AuditEvent[] events)
    {
        lock (_gate)
        {
            foreach (var e in events)
            {
                _rows[e.Id] = e;
            }
        }
    }

    public Task SaveEventAsync(AuditEvent auditEvent)
    {
        lock (_gate)
        {
            SaveCallCount++;
            if (SaveFailure is not null)
            {
                return Task.FromException(SaveFailure);
            }

            _rows[auditEvent.Id] = auditEvent;
        }

        return Task.CompletedTask;
    }

    public Task<AuditEvent?> GetEventAsync(string id)
    {
        lock (_gate)
        {
            return Task.FromResult(_rows.TryGetValue(id, out var found) ? found : null);
        }
    }

    public Task<AuditEventPage> QueryEventsAsync(
        string? userId, string? action, string? resourceType, string? resourceId,
        DateTime? from, DateTime? to, int page, int pageSize)
    {
        var matches = Rows
            .Where(e => userId is null || e.UserId == userId)
            .Where(e => action is null || e.Action == action)
            .Where(e => resourceType is null || e.ResourceType == resourceType)
            .Where(e => resourceId is null || e.ResourceId == resourceId)
            .Where(e => !from.HasValue || e.Timestamp >= from.Value)
            .Where(e => !to.HasValue || e.Timestamp <= to.Value)
            .OrderByDescending(e => e.Timestamp)
            .ToList();

        return Task.FromResult(new AuditEventPage
        {
            Events = matches.Skip(Math.Max(0, (page - 1) * pageSize)).Take(Math.Max(0, pageSize)).ToList(),
            Total = matches.Count,
            Page = page,
            PageSize = pageSize,
        });
    }

    public Task<List<AuditEvent>> GetAllUserEventsAsync(string userId) =>
        Task.FromResult(Rows.Where(e => e.UserId == userId).OrderByDescending(e => e.Timestamp).ToList());

    public Task<List<AuditEvent>> GetResourceHistoryAsync(string resourceId) =>
        Task.FromResult(Rows.Where(e => e.ResourceId == resourceId).OrderByDescending(e => e.Timestamp).ToList());

    public Task<List<AuditEvent>> GetEventsByDateRangeAsync(DateTime from, DateTime to) =>
        Task.FromResult(Rows
            .Where(e => e.Timestamp >= from && e.Timestamp <= to)
            .OrderByDescending(e => e.Timestamp)
            .ToList());

    public Task<int> DeleteEventsAsync(IEnumerable<string> eventIds)
    {
        lock (_gate)
        {
            var deleted = eventIds.Count(id => _rows.Remove(id));
            return Task.FromResult(deleted);
        }
    }
}
