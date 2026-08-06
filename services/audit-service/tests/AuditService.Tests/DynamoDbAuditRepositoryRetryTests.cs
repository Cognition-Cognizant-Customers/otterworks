using Amazon.DynamoDBv2;
using Amazon.DynamoDBv2.Model;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Moq;
using OtterWorks.AuditService.Config;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Batch-delete retry behaviour: DynamoDB may return items it did not process, and the
/// repository retries them up to five times before counting them as failures.
/// </summary>
public class DynamoDbAuditRepositoryRetryTests
{
    private readonly Mock<IAmazonDynamoDB> _mockDynamoDb = new();
    private readonly DynamoDbAuditRepository _repository;

    public DynamoDbAuditRepositoryRetryTests()
    {
        _repository = new DynamoDbAuditRepository(
            _mockDynamoDb.Object,
            Options.Create(new AwsSettings { DynamoDbTable = "test-audit-events", Region = "us-east-1" }),
            new Mock<ILogger<DynamoDbAuditRepository>>().Object);
    }

    [Fact]
    public async Task DeleteEventsAsync_UnprocessedItemsClearOnFourthRetry_ReportsEveryEventDeleted()
    {
        var eventIds = new[] { "event-1", "event-2", "event-3" };

        _mockDynamoDb
            .SetupSequence(d => d.BatchWriteItemAsync(It.IsAny<BatchWriteItemRequest>(), default))
            .ReturnsAsync(Unprocessed("event-3"))   // initial write
            .ReturnsAsync(Unprocessed("event-3"))   // retry 1
            .ReturnsAsync(Unprocessed("event-3"))   // retry 2
            .ReturnsAsync(Unprocessed("event-3"))   // retry 3
            .ReturnsAsync(AllProcessed());          // retry 4

        var deleted = await _repository.DeleteEventsAsync(eventIds);

        Assert.Equal(3, deleted);
        _mockDynamoDb.Verify(
            d => d.BatchWriteItemAsync(It.IsAny<BatchWriteItemRequest>(), default),
            Times.Exactly(5));
    }

    [Fact]
    public async Task DeleteEventsAsync_ItemsStillUnprocessedAfterFifthRetry_ExcludesThemFromTheDeletedCount()
    {
        var eventIds = new[] { "event-1", "event-2", "event-3", "event-4", "event-5" };

        _mockDynamoDb
            .Setup(d => d.BatchWriteItemAsync(It.IsAny<BatchWriteItemRequest>(), default))
            .ReturnsAsync(() => Unprocessed("event-4", "event-5"));

        var deleted = await _repository.DeleteEventsAsync(eventIds);

        Assert.Equal(3, deleted);
        _mockDynamoDb.Verify(
            d => d.BatchWriteItemAsync(It.IsAny<BatchWriteItemRequest>(), default),
            Times.Exactly(6));
    }

    [Fact]
    public async Task DeleteEventsAsync_RetryCarriesOnlyTheUnprocessedItems()
    {
        var requests = new List<BatchWriteItemRequest>();

        _mockDynamoDb
            .SetupSequence(d => d.BatchWriteItemAsync(Capture.In(requests), default))
            .ReturnsAsync(Unprocessed("event-2"))
            .ReturnsAsync(AllProcessed());

        await _repository.DeleteEventsAsync(new[] { "event-1", "event-2" });

        Assert.Equal(2, requests.Count);
        Assert.Equal(2, requests[0].RequestItems["test-audit-events"].Count);
        var retried = requests[1].RequestItems["test-audit-events"];
        Assert.Equal("event-2", Assert.Single(retried).DeleteRequest.Key["id"].S);
    }

    [Fact]
    public async Task DeleteEventsAsync_NoUnprocessedItems_WritesOncePerBatchOfTwentyFive()
    {
        var eventIds = Enumerable.Range(1, 26).Select(i => $"event-{i}").ToArray();

        _mockDynamoDb
            .Setup(d => d.BatchWriteItemAsync(It.IsAny<BatchWriteItemRequest>(), default))
            .ReturnsAsync(AllProcessed());

        var deleted = await _repository.DeleteEventsAsync(eventIds);

        Assert.Equal(26, deleted);
        _mockDynamoDb.Verify(
            d => d.BatchWriteItemAsync(It.IsAny<BatchWriteItemRequest>(), default),
            Times.Exactly(2));
    }

    private static BatchWriteItemResponse AllProcessed() => new();

    private static BatchWriteItemResponse Unprocessed(params string[] eventIds) => new()
    {
        UnprocessedItems = new Dictionary<string, List<WriteRequest>>
        {
            ["test-audit-events"] = eventIds.Select(id => new WriteRequest
            {
                DeleteRequest = new DeleteRequest
                {
                    Key = new Dictionary<string, AttributeValue>
                    {
                        ["id"] = new AttributeValue { S = id },
                    },
                },
            }).ToList(),
        },
    };
}
