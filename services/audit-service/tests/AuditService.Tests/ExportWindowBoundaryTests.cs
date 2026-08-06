using System.Net;
using System.Net.Http.Json;
using AuditService.Tests.Support;
using Microsoft.Extensions.Options;
using OtterWorks.AuditService.Config;
using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Export / archive window semantics. The window is applied by the store, so these cases run the
/// real repository and archiver against an in-memory DynamoDB whose comparisons are ordinal on the
/// serialized timestamp — exactly what DynamoDB does with an <c>S</c> attribute.
/// </summary>
public class ExportWindowBoundaryTests
{
    private static readonly DateTime WindowStart = new(2026, 6, 1, 0, 0, 0, DateTimeKind.Utc);
    private static readonly DateTime WindowEnd = new(2026, 6, 30, 0, 0, 0, DateTimeKind.Utc);

    private sealed record Stack(FakeDynamoDb Dynamo, FakeS3 S3, IAuditService Service);

    private static Stack BuildStack(params AuditEvent[] seed)
    {
        var dynamo = new FakeDynamoDb();
        dynamo.Seed(seed);

        var settings = Options.Create(new AwsSettings
        {
            DynamoDbTable = "test-audit-events",
            S3ArchiveBucket = "test-archive-bucket",
            ArchiveAfterDays = 90,
        });

        var repository = new DynamoDbAuditRepository(
            dynamo.Client, settings, new CapturingLogger<DynamoDbAuditRepository>());
        var s3 = new FakeS3();
        var archiver = new S3AuditArchiver(
            s3.Client, repository, settings, new CapturingLogger<S3AuditArchiver>());
        var service = new OtterWorks.AuditService.Services.AuditService(
            repository, archiver, settings, new CapturingLogger<OtterWorks.AuditService.Services.AuditService>());

        return new Stack(dynamo, s3, service);
    }

    // ------------------------------------------------------------ boundaries

    [Fact]
    public async Task WindowIsInclusiveAtBothEndsAndExcludesOneTickOutside()
    {
        var stack = BuildStack(
            FakeDynamoDb.Event("before", WindowStart.AddTicks(-1)),
            FakeDynamoDb.Event("at-start", WindowStart),
            FakeDynamoDb.Event("after-start", WindowStart.AddTicks(1)),
            FakeDynamoDb.Event("before-end", WindowEnd.AddTicks(-1)),
            FakeDynamoDb.Event("at-end", WindowEnd),
            FakeDynamoDb.Event("after-end", WindowEnd.AddTicks(1)));

        var result = await stack.Service.ExportAsync(WindowStart, WindowEnd, "json");

        Assert.Equal(4, result.EventCount);
        var exported = Assert.Single(stack.S3.PutRequests).ContentBody;
        Assert.Contains("at-start", exported, StringComparison.Ordinal);
        Assert.Contains("at-end", exported, StringComparison.Ordinal);
        Assert.DoesNotContain("\"before\"", exported, StringComparison.Ordinal);
        Assert.DoesNotContain("after-end", exported, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData(-1, false)]  // one tick before the start
    [InlineData(0, true)]    // exactly the start
    [InlineData(1, true)]    // one tick after the start
    public async Task StartBoundaryTrio(long tickOffset, bool included)
    {
        var stack = BuildStack(FakeDynamoDb.Event("candidate", WindowStart.AddTicks(tickOffset)));

        var result = await stack.Service.ExportAsync(WindowStart, WindowEnd, "json");

        Assert.Equal(included ? 1 : 0, result.EventCount);
    }

    [Theory]
    [InlineData(-1, true)]   // one tick before the end
    [InlineData(0, true)]    // exactly the end
    [InlineData(1, false)]   // one tick after the end
    public async Task EndBoundaryTrio(long tickOffset, bool included)
    {
        var stack = BuildStack(FakeDynamoDb.Event("candidate", WindowEnd.AddTicks(tickOffset)));

        var result = await stack.Service.ExportAsync(WindowStart, WindowEnd, "json");

        Assert.Equal(included ? 1 : 0, result.EventCount);
    }

    [Fact]
    public async Task ZeroWidthWindowMatchesOnlyTheExactInstant()
    {
        var stack = BuildStack(
            FakeDynamoDb.Event("just-before", WindowStart.AddTicks(-1)),
            FakeDynamoDb.Event("exact", WindowStart),
            FakeDynamoDb.Event("just-after", WindowStart.AddTicks(1)));

        var result = await stack.Service.ExportAsync(WindowStart, WindowStart, "json");

        Assert.Equal(1, result.EventCount);
        Assert.Contains("exact", Assert.Single(stack.S3.PutRequests).ContentBody, StringComparison.Ordinal);
    }

    [Fact]
    public async Task InvertedWindowExportsNothingInsteadOfFailing()
    {
        var stack = BuildStack(
            FakeDynamoDb.Event("inside", WindowStart.AddDays(3)));

        var result = await stack.Service.ExportAsync(WindowEnd, WindowStart, "json");

        // FINDING (audit-7): `to` earlier than `from` is accepted and silently yields an empty
        // export, which reads as "no activity in the period" to a compliance reviewer.
        Assert.Equal(0, result.EventCount);
        Assert.Equal(WindowEnd, result.From);
        Assert.Equal(WindowStart, result.To);
    }

    [Fact]
    public async Task InvertedWindowStillUploadsAnEmptyExportObject()
    {
        var stack = BuildStack(FakeDynamoDb.Event("inside", WindowStart.AddDays(3)));

        await stack.Service.ExportAsync(WindowEnd, WindowStart, "csv");

        var upload = Assert.Single(stack.S3.PutRequests);
        Assert.Equal("Id,Timestamp,UserId,Action,ResourceType,ResourceId,IpAddress,UserAgent",
            upload.ContentBody.Trim());
    }

    [Fact(Skip = "FINDING (audit-7): neither AuditController.ExportAuditLog nor the service validates the " +
                 "window, so from > to produces a 200 with an empty export rather than a 400. An empty " +
                 "export is indistinguishable from 'no events occurred'. Defect fix is a separate PR; do " +
                 "not fix in a coverage PR.")]
    public async Task InvertedWindow_ShouldBeRejected()
    {
        await using var app = await TestApp.StartAuditApiAsync(new RecordingAuditService());

        var response = await app.Client.GetAsync(
            "/api/v1/audit/export?from=2026-06-30T00:00:00Z&to=2026-06-01T00:00:00Z");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    // ------------------------------------------------------ DST / timezones

    [Fact]
    public async Task WindowSpanningTheUsDstTransitionKeepsEveryEventInTheGap()
    {
        // 2026-03-08 07:00Z is the US spring-forward instant (02:00 -> 03:00 local, EST -> EDT).
        var transition = new DateTime(2026, 3, 8, 7, 0, 0, DateTimeKind.Utc);
        var stack = BuildStack(
            FakeDynamoDb.Event("before-transition", transition.AddMinutes(-30)),
            FakeDynamoDb.Event("at-transition", transition),
            FakeDynamoDb.Event("after-transition", transition.AddMinutes(30)));

        var result = await stack.Service.ExportAsync(
            transition.AddHours(-1), transition.AddHours(1), "json");

        Assert.Equal(3, result.EventCount);
    }

    [Fact]
    public async Task WindowSpanningTheUsDstFallBackDoesNotDoubleCountTheRepeatedHour()
    {
        // 2026-11-01 06:00Z is the US fall-back instant; 01:00 local occurs twice.
        var transition = new DateTime(2026, 11, 1, 6, 0, 0, DateTimeKind.Utc);
        var stack = BuildStack(
            FakeDynamoDb.Event("first-pass", transition.AddMinutes(-30)),
            FakeDynamoDb.Event("second-pass", transition.AddMinutes(30)));

        var result = await stack.Service.ExportAsync(
            transition.AddHours(-2), transition.AddHours(2), "json");

        Assert.Equal(2, result.EventCount);
    }

    [Fact]
    public async Task WindowExpressedInALocalTimezoneMissesTheMatchingUtcRecords()
    {
        // A caller in America/New_York (UTC-4 in June) asking for "08:00 to 09:00" means
        // 12:00Z to 13:00Z. Query strings bind to DateTime with Kind=Unspecified.
        var stack = BuildStack(
            FakeDynamoDb.Event("noon-utc", new DateTime(2026, 6, 1, 12, 30, 0, DateTimeKind.Utc)));

        var localFrom = new DateTime(2026, 6, 1, 8, 0, 0, DateTimeKind.Unspecified);
        var localTo = new DateTime(2026, 6, 1, 9, 0, 0, DateTimeKind.Unspecified);

        var result = await stack.Service.ExportAsync(localFrom, localTo, "json");

        // FINDING (audit-8): the repository serializes the bounds with ToString("O") without
        // normalising to UTC, so an offset-free (or offset-bearing) bound is compared ordinally
        // against 'Z'-suffixed stored timestamps and the window silently selects the wrong rows.
        Assert.Equal(0, result.EventCount);
    }

    [Fact]
    public async Task BoundWithoutAZoneDropsARecordSittingExactlyOnTheWindowEnd()
    {
        var instant = new DateTime(2026, 6, 1, 12, 0, 0, DateTimeKind.Utc);
        var stack = BuildStack(FakeDynamoDb.Event("on-the-end", instant));

        var result = await stack.Service.ExportAsync(
            DateTime.SpecifyKind(instant.AddHours(-1), DateTimeKind.Unspecified),
            DateTime.SpecifyKind(instant, DateTimeKind.Unspecified),
            "json");

        // "2026-06-01T12:00:00.0000000Z" sorts after "2026-06-01T12:00:00.0000000", so the record
        // exactly on the boundary falls outside a zone-less window. Same root cause as audit-8.
        Assert.Equal(0, result.EventCount);
    }

    [Fact(Skip = "FINDING (audit-8): DynamoDbAuditRepository formats window bounds with ToString(\"O\") and " +
                 "compares them lexicographically against stored 'Z'-suffixed timestamps. A bound whose " +
                 "Kind is not Utc (which is what query-string binding produces) therefore selects the wrong " +
                 "rows — records are silently missing from compliance exports. Bounds should be converted " +
                 "with ToUniversalTime() before formatting. Defect fix is a separate PR; do not fix in a " +
                 "coverage PR.")]
    public async Task WindowExpressedInALocalTimezone_ShouldBeNormalisedToUtc()
    {
        var stack = BuildStack(
            FakeDynamoDb.Event("noon-utc", new DateTime(2026, 6, 1, 12, 30, 0, DateTimeKind.Utc)));

        var result = await stack.Service.ExportAsync(
            new DateTime(2026, 6, 1, 12, 0, 0, DateTimeKind.Unspecified),
            new DateTime(2026, 6, 1, 13, 0, 0, DateTimeKind.Unspecified),
            "json");

        Assert.Equal(1, result.EventCount);
    }

    [Fact]
    public async Task UtcBoundsAreSerialisedWithAZoneDesignator()
    {
        var stack = BuildStack();

        await stack.Service.ExportAsync(WindowStart, WindowEnd, "json");

        var scan = Assert.Single(stack.Dynamo.ScanRequests);
        Assert.EndsWith("Z", scan.ExpressionAttributeValues[":fromTs"].S, StringComparison.Ordinal);
        Assert.EndsWith("Z", scan.ExpressionAttributeValues[":toTs"].S, StringComparison.Ordinal);
    }

    // ------------------------------------------------------------- formats

    [Theory]
    [InlineData("json", "application/json")]
    [InlineData("JSON", "application/json")]
    [InlineData("csv", "text/csv")]
    [InlineData("CSV", "text/csv")]
    public async Task ExportUsesTheContentTypeMatchingTheRequestedFormat(string format, string contentType)
    {
        var stack = BuildStack(FakeDynamoDb.Event("e1", WindowStart.AddDays(1)));

        await stack.Service.ExportAsync(WindowStart, WindowEnd, format);

        Assert.Equal(contentType, Assert.Single(stack.S3.PutRequests).ContentType);
    }

    [Theory]
    [InlineData("xml")]
    [InlineData("")]
    [InlineData("json ")]
    public async Task ControllerRejectsAnUnsupportedExportFormat(string format)
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync($"/api/v1/audit/export?format={Uri.EscapeDataString(format)}");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty(service.ExportCalls);
    }

    [Fact]
    public async Task ControllerDefaultsToATrailingThirtyDayJsonWindow()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var before = DateTime.UtcNow;
        var response = await app.Client.GetAsync("/api/v1/audit/export");
        var after = DateTime.UtcNow;

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var call = Assert.Single(service.ExportCalls);
        Assert.Equal("json", call.Format);
        Assert.InRange(call.To, before, after);
        Assert.InRange(call.From, before.AddDays(-30), after.AddDays(-30));
    }

    [Fact]
    public async Task ControllerHonoursAnExplicitWindow()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var response = await app.Client.GetAsync(
            "/api/v1/audit/export?format=csv&from=2026-06-01T00:00:00Z&to=2026-06-30T00:00:00Z");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var call = Assert.Single(service.ExportCalls);
        Assert.Equal("csv", call.Format);
        Assert.Equal(WindowStart, call.From.ToUniversalTime());
        Assert.Equal(WindowEnd, call.To.ToUniversalTime());
    }

    [Fact]
    public async Task EmptyWindowExportReportsZeroEventsAndStillPublishesTheObject()
    {
        var stack = BuildStack();

        var result = await stack.Service.ExportAsync(WindowStart, WindowEnd, "json");

        Assert.Equal(0, result.EventCount);
        Assert.StartsWith("s3://test-archive-bucket/audit-exports/2026-06-01_2026-06-30_",
            result.DownloadUrl, StringComparison.Ordinal);
        Assert.Single(stack.S3.PutRequests);
    }

    // ------------------------------------------------------ archive window

    [Fact]
    public async Task ArchiveCutoffIsExactlyArchiveAfterDaysBeforeNow()
    {
        var repository = new InMemoryAuditRepository();
        var archiver = new CapturingArchiver();
        var service = new OtterWorks.AuditService.Services.AuditService(
            repository,
            archiver,
            Options.Create(new AwsSettings { ArchiveAfterDays = 90 }),
            new CapturingLogger<OtterWorks.AuditService.Services.AuditService>());

        var before = DateTime.UtcNow;
        await service.ArchiveOldEventsAsync();
        var after = DateTime.UtcNow;

        Assert.InRange(archiver.LastCutoff, before.AddDays(-90), after.AddDays(-90));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    [InlineData(365)]
    public async Task ArchiveCutoffTracksTheConfiguredRetention(int retentionDays)
    {
        var archiver = new CapturingArchiver();
        var service = new OtterWorks.AuditService.Services.AuditService(
            new InMemoryAuditRepository(),
            archiver,
            Options.Create(new AwsSettings { ArchiveAfterDays = retentionDays }),
            new CapturingLogger<OtterWorks.AuditService.Services.AuditService>());

        var before = DateTime.UtcNow;
        await service.ArchiveOldEventsAsync();
        var after = DateTime.UtcNow;

        Assert.InRange(archiver.LastCutoff, before.AddDays(-retentionDays), after.AddDays(-retentionDays));
    }

    [Fact]
    public async Task ArchivingAnEmptyWindowDoesNotTouchS3()
    {
        var stack = BuildStack(FakeDynamoDb.Event("fresh", WindowEnd));
        var s3 = new FakeS3();
        var archiver = new S3AuditArchiver(
            s3.Client,
            new DynamoDbAuditRepository(
                stack.Dynamo.Client,
                Options.Create(new AwsSettings { DynamoDbTable = "test-audit-events" }),
                new CapturingLogger<DynamoDbAuditRepository>()),
            Options.Create(new AwsSettings { S3ArchiveBucket = "test-archive-bucket" }),
            new CapturingLogger<S3AuditArchiver>());

        var result = await archiver.ArchiveOldEventsAsync(WindowStart);

        Assert.Equal(0, result.ArchivedCount);
        Assert.Empty(s3.PutRequests);
        Assert.Equal(string.Empty, result.S3Location);
    }

    private sealed class CapturingArchiver : IAuditArchiver
    {
        public DateTime LastCutoff { get; private set; }

        public Task<ExportResult> ExportAsync(DateTime from, DateTime to, string format) =>
            Task.FromResult(new ExportResult { Format = format, From = from, To = to });

        public Task<ArchiveResult> ArchiveOldEventsAsync(DateTime olderThan)
        {
            LastCutoff = olderThan;
            return Task.FromResult(new ArchiveResult { ArchivedBefore = olderThan });
        }
    }
}
