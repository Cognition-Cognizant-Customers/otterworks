using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests.TestDoubles;

public sealed class StubAuditArchiver : IAuditArchiver
{
    public Task<ExportResult> ExportAsync(DateTime from, DateTime to, string format)
        => Task.FromResult(new ExportResult
        {
            Format = format,
            EventCount = 0,
            DownloadUrl = "s3://test-bucket/export",
            From = from,
            To = to,
        });

    public Task<ArchiveResult> ArchiveOldEventsAsync(DateTime cutoff)
        => Task.FromResult(new ArchiveResult
        {
            ArchivedCount = 0,
            S3Location = "s3://test-bucket/archive",
            ArchivedBefore = cutoff,
        });
}
