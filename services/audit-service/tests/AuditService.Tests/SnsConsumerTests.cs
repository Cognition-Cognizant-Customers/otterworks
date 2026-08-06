using Amazon.SQS;
using Amazon.SQS.Model;
using AuditService.Tests.TestSupport;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Moq;
using OtterWorks.AuditService.Config;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests;

/// <summary>
/// Drives <see cref="SnsConsumer"/> against a stubbed SQS client. The stub hands out one
/// batch and then cancels the consumer, so no test waits on a clock.
/// </summary>
public class SnsConsumerTests
{
    private const string QueueName = "otterworks-audit-events-queue";
    private const string QueueUrl = "https://sqs.test/queue/otterworks-audit-events-queue";
    private static readonly TimeSpan DrainTimeout = TimeSpan.FromSeconds(30);

    private sealed record ConsumerRun(
        Mock<IAmazonSQS> Sqs,
        Mock<IAuditRepository> Repository,
        List<AuditEvent> Saved,
        RecordingLogger<SnsConsumer> Logger);

    private static Message MessageWith(string body, string messageId = "sqs-message-1", string receiptHandle = "receipt-1") =>
        new() { MessageId = messageId, ReceiptHandle = receiptHandle, Body = body };

    private static async Task<ConsumerRun> RunAsync(
        IReadOnlyList<Message> batch,
        Action<Mock<IAuditRepository>>? configureRepository = null,
        Action<Mock<IAmazonSQS>>? configureSqs = null)
    {
        var saved = new List<AuditEvent>();
        var repository = new Mock<IAuditRepository>();
        repository.Setup(r => r.SaveEventAsync(It.IsAny<AuditEvent>()))
            .Callback<AuditEvent>(saved.Add)
            .Returns(Task.CompletedTask);
        configureRepository?.Invoke(repository);

        var sqs = new Mock<IAmazonSQS>();
        sqs.Setup(c => c.GetQueueUrlAsync(QueueName, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new GetQueueUrlResponse { QueueUrl = QueueUrl });
        sqs.Setup(c => c.DeleteMessageAsync(QueueUrl, It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new DeleteMessageResponse());
        configureSqs?.Invoke(sqs);

        using var cts = new CancellationTokenSource();
        var drained = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var receiveCalls = 0;

        sqs.Setup(c => c.ReceiveMessageAsync(It.IsAny<ReceiveMessageRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(() =>
            {
                // First poll hands out the batch; the second ends the run, so the consumer
                // never blocks on SQS long-polling or a retry delay.
                if (Interlocked.Increment(ref receiveCalls) == 1)
                {
                    return new ReceiveMessageResponse { Messages = batch.ToList() };
                }

                cts.Cancel();
                drained.TrySetResult();
                return new ReceiveMessageResponse { Messages = new List<Message>() };
            });

        var logger = new RecordingLogger<SnsConsumer>();
        var consumer = new SnsConsumer(
            sqs.Object,
            repository.Object,
            Options.Create(new AwsSettings { Region = "us-east-1" }),
            logger);

        await consumer.StartAsync(cts.Token);
        await drained.Task.WaitAsync(DrainTimeout);
        await consumer.StopAsync(CancellationToken.None);
        consumer.Dispose();

        return new ConsumerRun(sqs, repository, saved, logger);
    }

    // ---------- positive ----------

    [Fact]
    public async Task PlainAuditEventPayload_IsPersistedAndTheMessageIsDeleted()
    {
        var run = await RunAsync(new[]
        {
            MessageWith("""
                {
                  "userId": "u-1",
                  "action": "delete",
                  "resourceType": "document",
                  "resourceId": "doc-9",
                  "ipAddress": "10.0.0.1",
                  "userAgent": "Otter/1.0",
                  "details": { "reason": "cleanup" },
                  "timestamp": "2026-03-01T10:00:00Z"
                }
                """),
        });

        var saved = Assert.Single(run.Saved);
        Assert.Equal("sqs-message-1", saved.Id);
        Assert.Equal("u-1", saved.UserId);
        Assert.Equal("delete", saved.Action);
        Assert.Equal("document", saved.ResourceType);
        Assert.Equal("doc-9", saved.ResourceId);
        Assert.Equal("10.0.0.1", saved.IpAddress);
        Assert.Equal("Otter/1.0", saved.UserAgent);
        Assert.Equal("cleanup", saved.Details!["reason"]);
        run.Sqs.Verify(c => c.DeleteMessageAsync(QueueUrl, "receipt-1", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task SnsEnvelope_IsUnwrappedBeforeDeserialization()
    {
        var run = await RunAsync(new[]
        {
            MessageWith("""
                {
                  "Type": "Notification",
                  "TopicArn": "arn:aws:sns:us-east-1:000000000000:otterworks-events",
                  "Message": "{\"userId\":\"u-2\",\"action\":\"login\",\"resourceType\":\"user\",\"resourceId\":\"u-2\"}"
                }
                """),
        });

        var saved = Assert.Single(run.Saved);
        Assert.Equal("u-2", saved.UserId);
        Assert.Equal("login", saved.Action);
    }

    [Fact]
    public async Task FileSharedEvent_IsRecordedAsAShareOfTheFile()
    {
        var run = await RunAsync(new[]
        {
            MessageWith("""
                {
                  "eventType": "file_shared",
                  "fileId": "file-1",
                  "ownerId": "owner-1",
                  "sharedWithUserId": "friend-1",
                  "timestamp": "2026-02-02T08:30:00Z"
                }
                """),
        });

        var saved = Assert.Single(run.Saved);
        Assert.Equal("share", saved.Action);
        Assert.Equal("file", saved.ResourceType);
        Assert.Equal("file-1", saved.ResourceId);
        Assert.Equal("owner-1", saved.UserId);
        Assert.Equal("friend-1", saved.Details!["sharedWithUserId"]);
        Assert.Equal(new DateTime(2026, 2, 2, 8, 30, 0, DateTimeKind.Utc), saved.Timestamp.ToUniversalTime());
    }

    [Fact]
    public async Task WholeBatch_IsProcessed()
    {
        var run = await RunAsync(new[]
        {
            MessageWith("""{"userId":"u-1","action":"create","resourceType":"file","resourceId":"f-1"}""", "m-1", "r-1"),
            MessageWith("""{"userId":"u-2","action":"create","resourceType":"file","resourceId":"f-2"}""", "m-2", "r-2"),
            MessageWith("""{"userId":"u-3","action":"create","resourceType":"file","resourceId":"f-3"}""", "m-3", "r-3"),
        });

        Assert.Equal(3, run.Saved.Count);
        Assert.Equal(new[] { "m-1", "m-2", "m-3" }, run.Saved.Select(e => e.Id));
        run.Sqs.Verify(
            c => c.DeleteMessageAsync(QueueUrl, It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Exactly(3));
    }

    [Fact]
    public async Task MissingQueue_IsCreatedBeforePolling()
    {
        var run = await RunAsync(
            new[] { MessageWith("""{"userId":"u-1","action":"create","resourceType":"file","resourceId":"f-1"}""") },
            configureSqs: sqs =>
            {
                sqs.Setup(c => c.GetQueueUrlAsync(QueueName, It.IsAny<CancellationToken>()))
                    .ThrowsAsync(new QueueDoesNotExistException("queue missing"));
                sqs.Setup(c => c.CreateQueueAsync(It.IsAny<CreateQueueRequest>(), It.IsAny<CancellationToken>()))
                    .ReturnsAsync(new CreateQueueResponse { QueueUrl = QueueUrl });
            });

        run.Sqs.Verify(
            c => c.CreateQueueAsync(It.Is<CreateQueueRequest>(r => r.QueueName == QueueName), It.IsAny<CancellationToken>()),
            Times.Once);
        Assert.Single(run.Saved);
    }

    // ---------- negative ----------

    [Theory]
    [InlineData("{ not json at all")]
    [InlineData("")]
    [InlineData("[1, 2, 3]")]
    [InlineData("null")]
    [InlineData("42")]
    public async Task MalformedPayload_IsNeitherPersistedNorDeleted_SoItCanBeRedelivered(string body)
    {
        var run = await RunAsync(new[] { MessageWith(body) });

        Assert.Empty(run.Saved);
        run.Sqs.Verify(
            c => c.DeleteMessageAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Never);
        Assert.Contains(run.Logger.Entries, e => e.Level == LogLevel.Error);
    }

    [Fact]
    public async Task SnsEnvelopeCarryingALiteralNull_IsDroppedFromTheQueueWithoutBeingPersisted()
    {
        var run = await RunAsync(new[] { MessageWith("""{"Type":"Notification","Message":"null"}""") });

        Assert.Empty(run.Saved);
        run.Sqs.Verify(c => c.DeleteMessageAsync(QueueUrl, "receipt-1", It.IsAny<CancellationToken>()), Times.Once);
        Assert.Contains(run.Logger.Entries, e => e.Level == LogLevel.Warning);
    }

    [Fact]
    public async Task UnknownEventType_IsStoredAsAnUnknownAction_NotRoutedToADlq()
    {
        // Pins current behaviour: an event type the consumer does not model still produces a
        // record, with "unknown" placeholders rather than being rejected. See WP-09 findings.
        var run = await RunAsync(new[]
        {
            MessageWith("""{"eventType":"file_quarantined","fileId":"file-7","ownerId":"owner-7"}"""),
        });

        var saved = Assert.Single(run.Saved);
        Assert.Equal("unknown", saved.Action);
        Assert.Equal("unknown", saved.ResourceType);
        Assert.Equal("system", saved.UserId);
        Assert.Equal(string.Empty, saved.ResourceId);
        run.Sqs.Verify(c => c.DeleteMessageAsync(QueueUrl, "receipt-1", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task PersistenceFailure_LeavesTheMessageOnTheQueue()
    {
        var run = await RunAsync(
            new[] { MessageWith("""{"userId":"u-1","action":"create","resourceType":"file","resourceId":"f-1"}""") },
            configureRepository: repo => repo
                .Setup(r => r.SaveEventAsync(It.IsAny<AuditEvent>()))
                .ThrowsAsync(new InvalidOperationException("dynamo unavailable")));

        run.Sqs.Verify(
            c => c.DeleteMessageAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Never);
        Assert.Contains(run.Logger.Entries, e => e.Level == LogLevel.Error);
    }

    [Fact]
    public async Task OnePoisonedMessage_DoesNotStopTheRestOfTheBatch()
    {
        var run = await RunAsync(new[]
        {
            MessageWith("{ broken", "m-bad", "r-bad"),
            MessageWith("""{"userId":"u-2","action":"create","resourceType":"file","resourceId":"f-2"}""", "m-good", "r-good"),
        });

        var saved = Assert.Single(run.Saved);
        Assert.Equal("m-good", saved.Id);
        run.Sqs.Verify(c => c.DeleteMessageAsync(QueueUrl, "r-good", It.IsAny<CancellationToken>()), Times.Once);
        run.Sqs.Verify(c => c.DeleteMessageAsync(QueueUrl, "r-bad", It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task QueueLookupFailure_StopsTheConsumerWithoutPolling()
    {
        var sqs = new Mock<IAmazonSQS>();
        sqs.Setup(c => c.GetQueueUrlAsync(QueueName, It.IsAny<CancellationToken>()))
            .ThrowsAsync(new AmazonSQSException("credentials rejected"));
        var repository = new Mock<IAuditRepository>();
        var logger = new RecordingLogger<SnsConsumer>();
        var consumer = new SnsConsumer(
            sqs.Object, repository.Object, Options.Create(new AwsSettings()), logger);

        await consumer.StartAsync(CancellationToken.None);
        await consumer.StopAsync(CancellationToken.None);
        consumer.Dispose();

        sqs.Verify(
            c => c.ReceiveMessageAsync(It.IsAny<ReceiveMessageRequest>(), It.IsAny<CancellationToken>()),
            Times.Never);
        repository.Verify(r => r.SaveEventAsync(It.IsAny<AuditEvent>()), Times.Never);
        Assert.Contains(logger.Entries, e => e.Level == LogLevel.Warning);
    }

    [Fact]
    public async Task EmptyBatch_PersistsNothing()
    {
        var run = await RunAsync(Array.Empty<Message>());

        Assert.Empty(run.Saved);
        run.Repository.Verify(r => r.SaveEventAsync(It.IsAny<AuditEvent>()), Times.Never);
    }

    [Fact]
    public async Task PayloadWithoutActor_IsAttributedToSystem()
    {
        var run = await RunAsync(new[]
        {
            MessageWith("""{"action":"create","resourceType":"file","resourceId":"f-1"}"""),
        });

        var saved = Assert.Single(run.Saved);
        Assert.Equal("system", saved.UserId);
    }

    [Fact]
    public async Task FileSharedEventWithoutOwner_IsAttributedToSystem()
    {
        var run = await RunAsync(new[]
        {
            MessageWith("""{"eventType":"file_shared","fileId":"file-1","sharedWithUserId":"friend-1"}"""),
        });

        var saved = Assert.Single(run.Saved);
        Assert.Equal("system", saved.UserId);
        Assert.Equal("share", saved.Action);
        Assert.Equal("friend-1", saved.Details!["sharedWithUserId"]);
    }

    // ---------- redelivery / idempotency ----------

    [Fact]
    public async Task Redelivery_OfTheSameMessageId_ReusesTheSameRecordId()
    {
        // SQS at-least-once delivery: the record id is the SQS message id, so a redelivered
        // message overwrites its own row instead of creating a second audit record.
        var run = await RunAsync(new[]
        {
            MessageWith("""{"userId":"u-1","action":"create","resourceType":"file","resourceId":"f-1"}""", "m-dup", "r-first"),
            MessageWith("""{"userId":"u-1","action":"create","resourceType":"file","resourceId":"f-1"}""", "m-dup", "r-second"),
        });

        Assert.Equal(2, run.Saved.Count);
        Assert.Single(run.Saved.Select(e => e.Id).Distinct());
        Assert.Equal("m-dup", run.Saved[0].Id);
    }

    // ---------- boundary ----------

    [Fact]
    public async Task PayloadWithoutTimestamp_IsStampedOnArrival()
    {
        var before = DateTime.UtcNow;
        var run = await RunAsync(new[]
        {
            MessageWith("""{"userId":"u-1","action":"create","resourceType":"file","resourceId":"f-1"}"""),
        });
        var after = DateTime.UtcNow;

        var saved = Assert.Single(run.Saved);
        Assert.InRange(saved.Timestamp, before, after);
    }

    [Fact]
    public async Task PayloadTimestamp_IsPreservedToTheTick()
    {
        var exact = new DateTime(2026, 4, 5, 6, 7, 8, DateTimeKind.Utc).AddTicks(1234567);
        var run = await RunAsync(new[]
        {
            MessageWith($$"""{"userId":"u-1","action":"create","resourceType":"file","resourceId":"f-1","timestamp":"{{exact:O}}"}"""),
        });

        var saved = Assert.Single(run.Saved);
        Assert.Equal(exact.Ticks, saved.Timestamp.ToUniversalTime().Ticks);
    }

    [Fact]
    public async Task PollingRequests_TheMaximumBatchSizeSqsAllows()
    {
        var run = await RunAsync(Array.Empty<Message>());

        run.Sqs.Verify(
            c => c.ReceiveMessageAsync(
                It.Is<ReceiveMessageRequest>(r => r.MaxNumberOfMessages == 10 && r.QueueUrl == QueueUrl),
                It.IsAny<CancellationToken>()),
            Times.AtLeastOnce);
    }
}
