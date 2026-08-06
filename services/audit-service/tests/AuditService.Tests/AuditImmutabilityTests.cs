using System.Net;
using System.Net.Http.Json;
using System.Reflection;
using System.Text;
using Amazon.DynamoDBv2;
using Amazon.DynamoDBv2.Model;
using AuditService.Tests.TestSupport;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Moq;
using OtterWorks.AuditService.Config;
using OtterWorks.AuditService.Models;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Audit records are compliance evidence: once written they must not be updatable or
/// individually deletable. These tests assert the absence of any such path — at the HTTP
/// surface, on the service contract, and in the DynamoDB calls the repository issues.
/// </summary>
public class AuditImmutabilityTests
{
    private static readonly string[] AllowedHttpMethods = { "GET", "POST" };

    private static IOptions<AwsSettings> Settings(int archiveAfterDays = 90) => Options.Create(new AwsSettings
    {
        Region = "us-east-1",
        DynamoDbTable = "test-table",
        S3ArchiveBucket = "test-bucket",
        ArchiveAfterDays = archiveAfterDays,
    });

    // ---------- no mutation route exists ----------

    [Theory]
    [InlineData("PUT", "/api/v1/audit/events/evt-1")]
    [InlineData("PATCH", "/api/v1/audit/events/evt-1")]
    [InlineData("DELETE", "/api/v1/audit/events/evt-1")]
    [InlineData("PUT", "/api/v1/audit/events")]
    [InlineData("DELETE", "/api/v1/audit/events")]
    [InlineData("DELETE", "/api/v1/audit/reports/compliance")]
    public async Task MutatingAnExistingAuditEvent_IsRejected(string method, string path)
    {
        await using var app = await TestAuditApp.StartAsync();

        var request = new HttpRequestMessage(new HttpMethod(method), path)
        {
            Content = new StringContent("{\"action\":\"tampered\"}", Encoding.UTF8, "application/json"),
        };
        var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.MethodNotAllowed, response.StatusCode);
        app.AuditService.VerifyNoOtherCalls();
    }

    [Fact]
    public async Task EveryMappedEndpoint_OnlyAcceptsGetOrPost()
    {
        await using var app = await TestAuditApp.StartAsync();

        var offenders = app.Endpoints
            .OfType<RouteEndpoint>()
            .Select(e => new
            {
                Route = e.RoutePattern.RawText,
                Methods = e.Metadata.GetMetadata<HttpMethodMetadata>()?.HttpMethods ?? (IReadOnlyList<string>)Array.Empty<string>(),
            })
            .Where(e => e.Methods.Any(m => !AllowedHttpMethods.Contains(m)))
            .Select(e => $"{e.Route}: {string.Join(",", e.Methods)}")
            .ToList();

        Assert.Empty(offenders);
    }

    [Fact]
    public void AuditServiceContract_ExposesNoUpdateOrSingleDeleteOperation()
    {
        var mutators = typeof(IAuditService)
            .GetMethods()
            .Select(m => m.Name)
            .Where(name =>
                name.StartsWith("Update", StringComparison.Ordinal) ||
                name.StartsWith("Delete", StringComparison.Ordinal) ||
                name.StartsWith("Remove", StringComparison.Ordinal) ||
                name.StartsWith("Patch", StringComparison.Ordinal) ||
                name.StartsWith("Modify", StringComparison.Ordinal) ||
                name.StartsWith("Purge", StringComparison.Ordinal))
            .ToList();

        Assert.Empty(mutators);
    }

    [Fact]
    public void AuditRepositoryContract_ExposesBatchDeleteOnly_ForArchival()
    {
        var mutators = typeof(IAuditRepository)
            .GetMethods()
            .Select(m => m.Name)
            .Where(name =>
                name.StartsWith("Update", StringComparison.Ordinal) ||
                name.StartsWith("Delete", StringComparison.Ordinal) ||
                name.StartsWith("Remove", StringComparison.Ordinal) ||
                name.StartsWith("Patch", StringComparison.Ordinal) ||
                name.StartsWith("Modify", StringComparison.Ordinal))
            .ToList();

        Assert.Equal(new[] { "DeleteEventsAsync" }, mutators);
    }

    [Fact]
    public void RecordRequestContract_HasNoIdField_SoACallerCannotTargetAnExistingRecord()
    {
        var idLikeProperties = typeof(AuditEventRequest)
            .GetProperties(BindingFlags.Public | BindingFlags.Instance)
            .Select(p => p.Name)
            .Where(name => name.Equals("Id", StringComparison.OrdinalIgnoreCase))
            .ToList();

        Assert.Empty(idLikeProperties);
    }

    // ---------- writes never mutate an existing record ----------

    [Fact]
    public async Task RecordingTheSameEventTwice_CreatesTwoDistinctRecords()
    {
        var saved = new List<AuditEvent>();
        var repository = new Mock<IAuditRepository>();
        repository.Setup(r => r.SaveEventAsync(It.IsAny<AuditEvent>()))
            .Callback<AuditEvent>(saved.Add)
            .Returns(Task.CompletedTask);
        var service = new OtterWorks.AuditService.Services.AuditService(
            repository.Object,
            Mock.Of<IAuditArchiver>(),
            Settings(),
            Mock.Of<ILogger<OtterWorks.AuditService.Services.AuditService>>());

        var request = new AuditEventRequest
        {
            UserId = "u-1",
            Action = "delete",
            ResourceType = "document",
            ResourceId = "doc-1",
        };

        var first = await service.RecordEventAsync(request);
        var second = await service.RecordEventAsync(request);

        Assert.NotEqual(first.Id, second.Id);
        Assert.Equal(2, saved.Count);
        Assert.Equal(2, saved.Select(e => e.Id).Distinct().Count());
    }

    [Fact]
    public async Task ReadOperations_NeverDeleteAnything()
    {
        var repository = new Mock<IAuditRepository>();
        repository.Setup(r => r.GetEventAsync(It.IsAny<string>())).ReturnsAsync((AuditEvent?)null);
        repository.Setup(r => r.QueryEventsAsync(
                It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTime?>(), It.IsAny<DateTime?>(), It.IsAny<int>(), It.IsAny<int>()))
            .ReturnsAsync(new AuditEventPage());
        repository.Setup(r => r.GetAllUserEventsAsync(It.IsAny<string>())).ReturnsAsync(new List<AuditEvent>());
        repository.Setup(r => r.GetResourceHistoryAsync(It.IsAny<string>())).ReturnsAsync(new List<AuditEvent>());
        repository.Setup(r => r.GetEventsByDateRangeAsync(It.IsAny<DateTime>(), It.IsAny<DateTime>()))
            .ReturnsAsync(new List<AuditEvent>());

        var service = new OtterWorks.AuditService.Services.AuditService(
            repository.Object,
            Mock.Of<IAuditArchiver>(),
            Settings(),
            Mock.Of<ILogger<OtterWorks.AuditService.Services.AuditService>>());

        await service.RecordEventAsync(new AuditEventRequest
        {
            UserId = "u-1",
            Action = "read",
            ResourceType = "document",
            ResourceId = "doc-1",
        });
        await service.GetEventAsync("evt-1");
        await service.QueryEventsAsync(null, null, null, null, null, null, 1, 20);
        await service.GetUserActivityReportAsync("u-1", "30d");
        await service.GetResourceHistoryAsync("res-1");
        await service.GetComplianceReportAsync("30d");

        repository.Verify(r => r.DeleteEventsAsync(It.IsAny<IEnumerable<string>>()), Times.Never);
    }

    [Fact]
    public async Task SavingAnEvent_UsesPutItem_AndNeverUpdateItem()
    {
        var dynamo = new Mock<IAmazonDynamoDB>(MockBehavior.Strict);
        dynamo.Setup(d => d.PutItemAsync(It.IsAny<PutItemRequest>(), default))
            .ReturnsAsync(new PutItemResponse());
        var repository = new DynamoDbAuditRepository(
            dynamo.Object, Settings(), Mock.Of<ILogger<DynamoDbAuditRepository>>());

        await repository.SaveEventAsync(new AuditEvent
        {
            Id = "evt-1",
            UserId = "u-1",
            Action = "create",
            ResourceType = "document",
            ResourceId = "doc-1",
            Timestamp = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc),
        });

        dynamo.Verify(d => d.PutItemAsync(It.IsAny<PutItemRequest>(), default), Times.Once);
        dynamo.VerifyNoOtherCalls();
    }

    // ---------- duplicate suppression ----------

    [Fact]
    public async Task TheSameEventIdSavedTwice_YieldsExactlyOneStoredRecord()
    {
        var store = new Dictionary<string, Dictionary<string, AttributeValue>>();
        var dynamo = new Mock<IAmazonDynamoDB>();
        dynamo.Setup(d => d.PutItemAsync(It.IsAny<PutItemRequest>(), default))
            .Callback<PutItemRequest, CancellationToken>((req, _) => store[req.Item["id"].S] = req.Item)
            .ReturnsAsync(new PutItemResponse());
        var repository = new DynamoDbAuditRepository(
            dynamo.Object, Settings(), Mock.Of<ILogger<DynamoDbAuditRepository>>());

        var duplicated = new AuditEvent
        {
            Id = "sns-message-1",
            UserId = "u-1",
            Action = "share",
            ResourceType = "file",
            ResourceId = "file-1",
            Timestamp = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc),
        };

        await repository.SaveEventAsync(duplicated);
        await repository.SaveEventAsync(duplicated);

        Assert.Single(store);
        Assert.Equal("sns-message-1", store.Keys.Single());
        dynamo.Verify(d => d.PutItemAsync(It.IsAny<PutItemRequest>(), default), Times.Exactly(2));
    }
}
