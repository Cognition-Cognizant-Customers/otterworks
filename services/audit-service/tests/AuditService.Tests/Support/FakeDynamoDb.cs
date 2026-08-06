using Amazon.DynamoDBv2;
using Amazon.DynamoDBv2.Model;
using Moq;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests.Support;

/// <summary>
/// In-memory stand-in for a DynamoDB table. Filtering deliberately mirrors DynamoDB's own
/// semantics for <c>S</c> attributes: comparisons are ordinal on the stored string, which is
/// what makes timestamp-window behaviour (and any timezone handling in the repository)
/// observable in tests. No live AWS or LocalStack is involved.
/// </summary>
public sealed class FakeDynamoDb
{
    private readonly Dictionary<string, Dictionary<string, AttributeValue>> _items = new(StringComparer.Ordinal);

    public FakeDynamoDb()
    {
        Mock = new Mock<IAmazonDynamoDB>(MockBehavior.Strict);

        Mock.Setup(d => d.PutItemAsync(It.IsAny<PutItemRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((PutItemRequest req, CancellationToken _) =>
            {
                PutRequests.Add(req);
                _items[req.Item["id"].S] = req.Item;
                return new PutItemResponse();
            });

        Mock.Setup(d => d.GetItemAsync(It.IsAny<GetItemRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((GetItemRequest req, CancellationToken _) =>
            {
                var key = req.Key["id"].S;
                return new GetItemResponse
                {
                    Item = _items.TryGetValue(key, out var item)
                        ? item
                        : new Dictionary<string, AttributeValue>(),
                };
            });

        Mock.Setup(d => d.ScanAsync(It.IsAny<ScanRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((ScanRequest req, CancellationToken _) =>
            {
                ScanRequests.Add(req);
                return new ScanResponse
                {
                    Items = _items.Values.Where(item => Matches(item, req)).ToList(),
                    LastEvaluatedKey = new Dictionary<string, AttributeValue>(),
                };
            });

        Mock.Setup(d => d.BatchWriteItemAsync(It.IsAny<BatchWriteItemRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((BatchWriteItemRequest req, CancellationToken _) =>
            {
                foreach (var write in req.RequestItems.Values.SelectMany(v => v))
                {
                    if (write.DeleteRequest is not null)
                    {
                        _items.Remove(write.DeleteRequest.Key["id"].S);
                    }
                }

                return new BatchWriteItemResponse
                {
                    UnprocessedItems = new Dictionary<string, List<WriteRequest>>(),
                };
            });
    }

    public Mock<IAmazonDynamoDB> Mock { get; }

    public IAmazonDynamoDB Client => Mock.Object;

    public List<ScanRequest> ScanRequests { get; } = new();

    public List<PutItemRequest> PutRequests { get; } = new();

    public int RowCount => _items.Count;

    public IReadOnlyCollection<string> Ids => _items.Keys.ToList();

    public void Seed(params AuditEvent[] events)
    {
        foreach (var e in events)
        {
            _items[e.Id] = ToItem(e);
        }
    }

    public static AuditEvent Event(string id, DateTime timestamp, string userId = "user-a", string action = "read") =>
        new()
        {
            Id = id,
            UserId = userId,
            Action = action,
            ResourceType = "document",
            ResourceId = "doc-1",
            Timestamp = timestamp,
        };

    private static Dictionary<string, AttributeValue> ToItem(AuditEvent e) => new()
    {
        ["id"] = new AttributeValue { S = e.Id },
        ["Id"] = new AttributeValue { S = e.Id },
        ["UserId"] = new AttributeValue { S = e.UserId },
        ["Action"] = new AttributeValue { S = e.Action },
        ["ResourceType"] = new AttributeValue { S = e.ResourceType },
        ["ResourceId"] = new AttributeValue { S = e.ResourceId },
        ["Timestamp"] = new AttributeValue { S = e.Timestamp.ToString("O") },
    };

    private static bool Matches(Dictionary<string, AttributeValue> item, ScanRequest req)
    {
        var values = req.ExpressionAttributeValues;
        if (values is null || values.Count == 0)
        {
            return true;
        }

        if (values.TryGetValue(":uid", out var uid) && item["UserId"].S != uid.S)
        {
            return false;
        }

        if (values.TryGetValue(":act", out var act) && item["Action"].S != act.S)
        {
            return false;
        }

        if (values.TryGetValue(":rt", out var rt) && item["ResourceType"].S != rt.S)
        {
            return false;
        }

        if (values.TryGetValue(":rid", out var rid) && item["ResourceId"].S != rid.S)
        {
            return false;
        }

        var timestamp = item["Timestamp"].S;

        if (values.TryGetValue(":fromTs", out var from) &&
            string.CompareOrdinal(timestamp, from.S) < 0)
        {
            return false;
        }

        if (values.TryGetValue(":toTs", out var to) &&
            string.CompareOrdinal(timestamp, to.S) > 0)
        {
            return false;
        }

        return true;
    }
}
