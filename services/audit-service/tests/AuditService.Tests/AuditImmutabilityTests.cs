using System.Net;
using System.Net.Http.Json;
using System.Reflection;
using AuditService.Tests.Support;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using OtterWorks.AuditService.Config;
using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// An audit record must be append-only: no route, service method or repository call may alter or
/// remove one, apart from the archival sweep that moves aged records to Glacier.
/// </summary>
public class AuditImmutabilityTests
{
    private static readonly string[] MutationVerbs = { "PUT", "PATCH", "DELETE" };

    private static DynamoDbAuditRepository Repository(FakeDynamoDb dynamo) =>
        new(
            dynamo.Client,
            Options.Create(new AwsSettings { DynamoDbTable = "test-audit-events" }),
            new CapturingLogger<DynamoDbAuditRepository>());

    // ------------------------------------------------------- surface area

    [Fact]
    public async Task NoRouteExposesAMutatingVerb()
    {
        await using var app = await TestApp.StartAuditApiAsync(new RecordingAuditService());

        var mutating = app.Endpoints
            .Select(e => (Endpoint: e, Methods: e.Metadata.GetMetadata<HttpMethodMetadata>()?.HttpMethods ?? Array.Empty<string>()))
            .Where(x => x.Methods.Any(m => MutationVerbs.Contains(m, StringComparer.OrdinalIgnoreCase)))
            .Select(x => x.Endpoint.DisplayName)
            .ToList();

        Assert.Empty(mutating);
    }

    [Fact]
    public async Task TheOnlyWriteRoutesAreAppendAndArchive()
    {
        await using var app = await TestApp.StartAuditApiAsync(new RecordingAuditService());

        var postRoutes = app.Endpoints
            .OfType<RouteEndpoint>()
            .Where(e => (e.Metadata.GetMetadata<HttpMethodMetadata>()?.HttpMethods ?? Array.Empty<string>())
                .Contains("POST", StringComparer.OrdinalIgnoreCase))
            .Select(e => e.RoutePattern.RawText)
            .OrderBy(p => p, StringComparer.Ordinal)
            .ToList();

        Assert.Equal(new[] { "/api/v1/audit/archive", "/api/v1/audit/events" }, postRoutes);
    }

    [Fact]
    public void TheServiceContractExposesNoUpdateOrDeleteOperation()
    {
        var mutators = typeof(IAuditService)
            .GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .Select(m => m.Name)
            .Where(name =>
                name.Contains("Update", StringComparison.OrdinalIgnoreCase) ||
                name.Contains("Delete", StringComparison.OrdinalIgnoreCase) ||
                name.Contains("Modify", StringComparison.OrdinalIgnoreCase) ||
                name.Contains("Purge", StringComparison.OrdinalIgnoreCase))
            .ToList();

        Assert.Empty(mutators);
    }

    [Fact]
    public void TheOnlyRepositoryMutationIsTheArchivalDelete()
    {
        var mutators = typeof(IAuditRepository)
            .GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .Select(m => m.Name)
            .Where(name =>
                name.Contains("Update", StringComparison.OrdinalIgnoreCase) ||
                name.Contains("Delete", StringComparison.OrdinalIgnoreCase))
            .ToList();

        // DeleteEventsAsync exists solely so S3AuditArchiver can retire records it has already
        // written to Glacier; nothing on IAuditService or the HTTP surface can reach it.
        Assert.Equal(new[] { "DeleteEventsAsync" }, mutators);
    }

    // ---------------------------------------------- direct attempts fail

    [Theory]
    [InlineData("PUT")]
    [InlineData("PATCH")]
    [InlineData("DELETE")]
    public async Task MutatingAnExistingEventOverHttpIsRejected(string verb)
    {
        var service = new RecordingAuditService
        {
            StoredEvent = new AuditEvent { Id = "e1", UserId = "user-a" },
        };
        await using var app = await TestApp.StartAuditApiAsync(service);

        var request = new HttpRequestMessage(new HttpMethod(verb), "/api/v1/audit/events/e1")
        {
            Content = JsonContent.Create(new { userId = "attacker" }),
        };
        var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.MethodNotAllowed, response.StatusCode);
        Assert.Empty(service.GetEventCalls);
    }

    [Theory]
    [InlineData("PUT")]
    [InlineData("PATCH")]
    [InlineData("DELETE")]
    public async Task MutatingTheEventCollectionOverHttpIsRejected(string verb)
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var request = new HttpRequestMessage(new HttpMethod(verb), "/api/v1/audit/events");
        var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.MethodNotAllowed, response.StatusCode);
        Assert.Empty(service.QueryCalls);
    }

    [Fact]
    public async Task RecordingTheSameEventTwiceAppendsTwoRowsRatherThanUpdatingOne()
    {
        var service = new RecordingAuditService();
        await using var app = await TestApp.StartAuditApiAsync(service);

        var payload = new AuditEventRequest
        {
            UserId = "user-a",
            Action = "delete",
            ResourceType = "document",
            ResourceId = "doc-1",
        };

        var first = await app.Client.PostAsJsonAsync("/api/v1/audit/events", payload);
        var second = await app.Client.PostAsJsonAsync("/api/v1/audit/events", payload);

        var firstBody = await first.Content.ReadFromJsonAsync<AuditEventResponse>();
        var secondBody = await second.Content.ReadFromJsonAsync<AuditEventResponse>();

        Assert.NotEqual(firstBody!.Id, secondBody!.Id);
        Assert.Equal(2, service.RecordCalls.Count);
    }

    [Fact]
    public async Task RecordEventAlwaysMintsAServerSideIdAndTimestamp()
    {
        var dynamo = new FakeDynamoDb();
        var service = new OtterWorks.AuditService.Services.AuditService(
            Repository(dynamo),
            new S3AuditArchiver(
                new FakeS3().Client,
                Repository(dynamo),
                Options.Create(new AwsSettings()),
                new CapturingLogger<S3AuditArchiver>()),
            Options.Create(new AwsSettings()),
            new CapturingLogger<OtterWorks.AuditService.Services.AuditService>());

        var before = DateTime.UtcNow;
        var response = await service.RecordEventAsync(new AuditEventRequest
        {
            UserId = "user-a",
            Action = "create",
            ResourceType = "document",
            ResourceId = "doc-1",
        });
        var after = DateTime.UtcNow;

        Assert.True(Guid.TryParse(response.Id, out _));
        Assert.InRange(response.Timestamp, before, after);
        Assert.Equal(response.Id, Assert.Single(dynamo.PutRequests).Item["id"].S);
    }

    // ------------------------------------------------- storage-level guard

    [Fact]
    public async Task SavingAnEventIdThatAlreadyExistsOverwritesTheStoredRecord()
    {
        var dynamo = new FakeDynamoDb();
        var repository = Repository(dynamo);

        await repository.SaveEventAsync(new AuditEvent
        {
            Id = "e1",
            UserId = "user-a",
            Action = "delete",
            ResourceType = "document",
            ResourceId = "doc-1",
            Timestamp = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc),
        });

        await repository.SaveEventAsync(new AuditEvent
        {
            Id = "e1",
            UserId = "attacker",
            Action = "read",
            ResourceType = "document",
            ResourceId = "doc-1",
            Timestamp = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc),
        });

        // FINDING (audit-6): SaveEventAsync issues an unconditional PutItem, so anything able to
        // choose an event id can rewrite history in place. It is also what makes duplicate SNS
        // delivery collapse to one row, so the two behaviours must be fixed together.
        var stored = await repository.GetEventAsync("e1");
        Assert.Equal("attacker", stored!.UserId);
        Assert.Equal(1, dynamo.RowCount);
        Assert.All(dynamo.PutRequests, put => Assert.Null(put.ConditionExpression));
    }

    [Fact(Skip = "FINDING (audit-6): DynamoDbAuditRepository.SaveEventAsync writes with an unconditional " +
                 "PutItem, so a save for an existing id silently replaces the stored audit record — an " +
                 "append-only store must reject it (ConditionExpression attribute_not_exists(id)) and let " +
                 "the caller treat the conditional failure as a duplicate. Defect fix is a separate PR; " +
                 "do not fix in a coverage PR.")]
    public async Task SavingAnEventIdThatAlreadyExists_ShouldBeRefusedByTheStore()
    {
        var dynamo = new FakeDynamoDb();
        var repository = Repository(dynamo);

        await repository.SaveEventAsync(new AuditEvent { Id = "e1", UserId = "user-a" });

        Assert.All(
            dynamo.PutRequests,
            put => Assert.Equal("attribute_not_exists(id)", put.ConditionExpression));
    }

    [Fact]
    public async Task ArchivalDeletionRemovesOnlyTheRecordsItArchived()
    {
        var dynamo = new FakeDynamoDb();
        var cutoff = new DateTime(2026, 6, 1, 0, 0, 0, DateTimeKind.Utc);
        dynamo.Seed(
            FakeDynamoDb.Event("old-1", cutoff.AddDays(-1)),
            FakeDynamoDb.Event("old-2", cutoff.AddTicks(-1)),
            FakeDynamoDb.Event("boundary", cutoff),
            FakeDynamoDb.Event("fresh", cutoff.AddTicks(1)));

        var s3 = new FakeS3();
        var archiver = new S3AuditArchiver(
            s3.Client,
            Repository(dynamo),
            Options.Create(new AwsSettings { S3ArchiveBucket = "test-archive-bucket" }),
            new CapturingLogger<S3AuditArchiver>());

        var result = await archiver.ArchiveOldEventsAsync(cutoff);

        Assert.Equal(3, result.ArchivedCount);
        Assert.Equal(new[] { "fresh" }, dynamo.Ids);
        Assert.Equal("GLACIER", Assert.Single(s3.PutRequests).StorageClass.Value);
    }
}
