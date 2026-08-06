using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests.Support;

/// <summary>
/// Fake <see cref="IAuditService"/> that records exactly what the controller forwarded, so
/// controller-level defaulting, clamping and validation can be asserted in isolation.
/// </summary>
public sealed class RecordingAuditService : IAuditService
{
    public sealed record QueryCall(
        string? UserId,
        string? Action,
        string? ResourceType,
        string? ResourceId,
        DateTime? From,
        DateTime? To,
        int Page,
        int PageSize);

    public sealed record ExportCall(DateTime From, DateTime To, string Format);

    public List<AuditEventRequest> RecordCalls { get; } = new();

    public List<string> GetEventCalls { get; } = new();

    public List<QueryCall> QueryCalls { get; } = new();

    public List<(string UserId, string Period)> UserReportCalls { get; } = new();

    public List<string> ResourceHistoryCalls { get; } = new();

    public List<string> ComplianceCalls { get; } = new();

    public List<ExportCall> ExportCalls { get; } = new();

    public int ArchiveCalls { get; private set; }

    public AuditEvent? StoredEvent { get; set; }

    public AuditEventPage PageResult { get; set; } = new();

    public Exception? Failure { get; set; }

    public Task<AuditEventResponse> RecordEventAsync(AuditEventRequest request)
    {
        RecordCalls.Add(request);
        ThrowIfConfigured();

        var entity = new AuditEvent
        {
            Id = $"evt-{RecordCalls.Count:D3}",
            UserId = request.UserId,
            Action = request.Action,
            ResourceType = request.ResourceType,
            ResourceId = request.ResourceId,
            Details = request.Details,
            IpAddress = request.IpAddress,
            UserAgent = request.UserAgent,
            Timestamp = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc),
        };

        return Task.FromResult(AuditEventResponse.FromEntity(entity));
    }

    public Task<AuditEventResponse?> GetEventAsync(string id)
    {
        GetEventCalls.Add(id);
        ThrowIfConfigured();
        return Task.FromResult(StoredEvent is not null && StoredEvent.Id == id
            ? AuditEventResponse.FromEntity(StoredEvent)
            : null);
    }

    public Task<AuditEventPage> QueryEventsAsync(
        string? userId, string? action, string? resourceType, string? resourceId,
        DateTime? from, DateTime? to, int page, int pageSize)
    {
        QueryCalls.Add(new QueryCall(userId, action, resourceType, resourceId, from, to, page, pageSize));
        ThrowIfConfigured();
        return Task.FromResult(PageResult);
    }

    public Task<UserActivityReport> GetUserActivityReportAsync(string userId, string period)
    {
        UserReportCalls.Add((userId, period));
        ThrowIfConfigured();
        return Task.FromResult(new UserActivityReport { UserId = userId, Period = period });
    }

    public Task<ResourceHistory> GetResourceHistoryAsync(string resourceId)
    {
        ResourceHistoryCalls.Add(resourceId);
        ThrowIfConfigured();
        return Task.FromResult(new ResourceHistory { ResourceId = resourceId });
    }

    public Task<ComplianceReport> GetComplianceReportAsync(string period)
    {
        ComplianceCalls.Add(period);
        ThrowIfConfigured();
        return Task.FromResult(new ComplianceReport { Period = period });
    }

    public Task<ExportResult> ExportAsync(DateTime from, DateTime to, string format)
    {
        ExportCalls.Add(new ExportCall(from, to, format));
        ThrowIfConfigured();
        return Task.FromResult(new ExportResult { Format = format, From = from, To = to });
    }

    public Task<ArchiveResult> ArchiveOldEventsAsync()
    {
        ArchiveCalls++;
        ThrowIfConfigured();
        return Task.FromResult(new ArchiveResult { ArchivedCount = 0 });
    }

    private void ThrowIfConfigured()
    {
        if (Failure is not null)
        {
            throw Failure;
        }
    }
}
