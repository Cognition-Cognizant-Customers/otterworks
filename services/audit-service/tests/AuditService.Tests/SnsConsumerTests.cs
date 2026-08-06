using Amazon.SQS;
using Amazon.SQS.Model;
using AuditService.Tests.Support;
using Microsoft.Extensions.Logging;
using Moq;

namespace AuditService.Tests;

/// <summary>
/// Behaviour of the SNS/SQS ingestion path. Everything runs against a fake SQS client and an
/// in-memory repository keyed by event id (DynamoDB <c>PutItem</c> semantics) — no LocalStack.
/// </summary>
public class SnsConsumerTests
{
    private const string RawAuditEvent = """
        {
          "userId": "user-a",
          "action": "delete",
          "resourceType": "document",
          "resourceId": "doc-9",
          "ipAddress": "10.0.0.7",
          "userAgent": "otter/1.0",
          "timestamp": "2026-01-02T03:04:05Z"
        }
        """;

    private const string FileSharedEvent = """
        {
          "eventType": "file_shared",
          "fileId": "file-42",
          "ownerId": "user-owner",
          "sharedWithUserId": "user-guest",
          "timestamp": "2026-01-02T03:04:05Z"
        }
        """;

    // ---------------------------------------------------------------- positive

    [Fact]
    public async Task ValidRawMessage_IsPersistedAndAcknowledged()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(SnsConsumerHarness.Message(RawAuditEvent, "msg-raw"));

        var row = Assert.Single(harness.Repository.Rows);
        Assert.Equal("msg-raw", row.Id);
        Assert.Equal("user-a", row.UserId);
        Assert.Equal("delete", row.Action);
        Assert.Equal("document", row.ResourceType);
        Assert.Equal("doc-9", row.ResourceId);
        Assert.Equal("10.0.0.7", row.IpAddress);
        Assert.Equal("otter/1.0", row.UserAgent);
        Assert.Equal(new DateTime(2026, 1, 2, 3, 4, 5, DateTimeKind.Utc), row.Timestamp);
        Assert.Equal(("https://sqs.test/otterworks-audit-events-queue", "receipt-for-msg-raw"),
            Assert.Single(harness.DeletedReceiptHandles));
    }

    [Fact]
    public async Task ValidMessageInsideSnsEnvelope_IsUnwrappedAndPersisted()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(
            SnsConsumerHarness.Message(SnsConsumerHarness.SnsEnvelope(RawAuditEvent), "msg-envelope"));

        var row = Assert.Single(harness.Repository.Rows);
        Assert.Equal("user-a", row.UserId);
        Assert.Equal("delete", row.Action);
        Assert.Single(harness.DeletedReceiptHandles);
    }

    [Fact]
    public async Task FileSharedEvent_IsMappedToAShareAuditRecord()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(SnsConsumerHarness.Message(FileSharedEvent, "msg-share"));

        var row = Assert.Single(harness.Repository.Rows);
        Assert.Equal("user-owner", row.UserId);
        Assert.Equal("share", row.Action);
        Assert.Equal("file", row.ResourceType);
        Assert.Equal("file-42", row.ResourceId);
        Assert.Equal("user-guest", Assert.Contains("sharedWithUserId", row.Details!));
        Assert.Single(harness.DeletedReceiptHandles);
    }

    [Fact]
    public async Task FileSharedEventWithoutOwner_FallsBackToSystemActor()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(SnsConsumerHarness.Message(
            """{"eventType":"file_shared","fileId":"file-1"}""", "msg-share-anon"));

        var row = Assert.Single(harness.Repository.Rows);
        Assert.Equal("system", row.UserId);
        Assert.Equal(string.Empty, Assert.Contains("sharedWithUserId", row.Details!));
    }

    [Fact]
    public async Task BatchOfMessages_IsProcessedInFull()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(
            SnsConsumerHarness.Message(RawAuditEvent, "msg-1"),
            SnsConsumerHarness.Message(FileSharedEvent, "msg-2"),
            SnsConsumerHarness.Message(RawAuditEvent, "msg-3"));

        Assert.Equal(3, harness.Repository.RowCount);
        Assert.Equal(3, harness.DeletedReceiptHandles.Count);
    }

    [Fact]
    public async Task QueueIsCreated_WhenItDoesNotExistYet()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunWithQueueLookupAsync(
            () => throw new QueueDoesNotExistException("missing"),
            createQueue: true);

        harness.Sqs.Verify(
            s => s.CreateQueueAsync(
                It.Is<CreateQueueRequest>(r => r.QueueName == "otterworks-audit-events-queue"),
                It.IsAny<CancellationToken>()),
            Times.Once);
    }

    // ---------------------------------------------------------------- negative

    [Fact]
    public async Task MalformedJson_IsNotPersistedAndNotAcknowledged()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(SnsConsumerHarness.Message("{not json at all", "msg-bad"));

        Assert.Equal(0, harness.Repository.RowCount);
        Assert.Empty(harness.DeletedReceiptHandles);
        Assert.True(harness.Logger.HasLevel(LogLevel.Error));
    }

    [Fact]
    public async Task EnvelopeCarryingJsonNull_IsAcknowledgedAndDropped()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(SnsConsumerHarness.Message(
            SnsConsumerHarness.SnsEnvelope("null"), "msg-null"));

        Assert.Equal(0, harness.Repository.RowCount);
        // The consumer acknowledges (deletes) a payload it could not deserialise, so the event is
        // dropped silently rather than retried or dead-lettered.
        Assert.Single(harness.DeletedReceiptHandles);
        Assert.True(harness.Logger.HasLevel(LogLevel.Warning));
    }

    [Fact]
    public async Task NonObjectJsonPayload_IsTreatedAsPoisonAndNotAcknowledged()
    {
        var harness = new SnsConsumerHarness();

        // Envelope sniffing calls TryGetProperty on the root element, which throws for any
        // non-object JSON document; the message therefore takes the error path.
        await harness.RunAsync(SnsConsumerHarness.Message("null", "msg-scalar"));

        Assert.Equal(0, harness.Repository.RowCount);
        Assert.Empty(harness.DeletedReceiptHandles);
        Assert.True(harness.Logger.HasLevel(LogLevel.Error));
    }

    [Fact]
    public async Task MissingRequiredAttributes_AreDefaultedRatherThanRejected()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(SnsConsumerHarness.Message("{}", "msg-empty"));

        var row = Assert.Single(harness.Repository.Rows);
        Assert.Equal("system", row.UserId);
        Assert.Equal("unknown", row.Action);
        Assert.Equal("unknown", row.ResourceType);
        Assert.Equal(string.Empty, row.ResourceId);
        Assert.Single(harness.DeletedReceiptHandles);
    }

    [Fact]
    public async Task UnknownEventType_IsStoredAsAnUnknownActionRatherThanRouted()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(SnsConsumerHarness.Message(
            """{"eventType":"file_shredded","fileId":"file-7"}""", "msg-unknown"));

        var row = Assert.Single(harness.Repository.Rows);
        Assert.Equal("unknown", row.Action);
        Assert.Equal("unknown", row.ResourceType);
        Assert.Single(harness.DeletedReceiptHandles);
    }

    [Fact]
    public async Task RepositoryFailure_LeavesTheMessageOnTheQueueForRedelivery()
    {
        var harness = new SnsConsumerHarness();
        harness.Repository.SaveFailure = new InvalidOperationException("dynamo unavailable");

        await harness.RunAsync(SnsConsumerHarness.Message(RawAuditEvent, "msg-fail"));

        Assert.Equal(0, harness.Repository.RowCount);
        Assert.Empty(harness.DeletedReceiptHandles);
        Assert.True(harness.Logger.HasLevel(LogLevel.Error));
    }

    [Fact]
    public async Task ReceiveFailure_IsLoggedAndDoesNotTerminateTheConsumer()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunWithReceiveFailureAsync(new AmazonSQSException("throttled"));

        Assert.True(harness.Logger.HasLevel(LogLevel.Error));
        Assert.Equal(0, harness.Repository.RowCount);
    }

    [Fact]
    public async Task QueueInitialisationFailure_StopsProcessingWithAWarning()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunWithQueueLookupAsync(() => throw new AmazonSQSException("no credentials"));

        Assert.True(harness.Logger.HasLevel(LogLevel.Warning));
        harness.Sqs.Verify(
            s => s.ReceiveMessageAsync(It.IsAny<ReceiveMessageRequest>(), It.IsAny<CancellationToken>()),
            Times.Never);
    }

    [Fact]
    public async Task SubscriptionConfirmation_IsNeitherStoredNorAcknowledged()
    {
        var harness = new SnsConsumerHarness();
        var confirmation = SnsConsumerHarness.SnsEnvelope(
            "You have chosen to subscribe to the topic arn:aws:sns:us-east-1:000000000000:otterworks-events.",
            type: "SubscriptionConfirmation");

        await harness.RunAsync(SnsConsumerHarness.Message(confirmation, "msg-confirm"));

        Assert.Equal(0, harness.Repository.RowCount);
        // FINDING (audit-2): control-plane messages are never acknowledged, so a
        // SubscriptionConfirmation becomes a poison message that is redelivered forever.
        Assert.Empty(harness.DeletedReceiptHandles);
        Assert.True(harness.Logger.HasLevel(LogLevel.Error));
    }

    [Fact(Skip = "FINDING (audit-2): SnsConsumer has no branch for SNS control-plane messages. " +
                 "A SubscriptionConfirmation/UnsubscribeConfirmation body fails JSON deserialisation, " +
                 "is logged as an error and is never deleted, so SQS redelivers it until the retention " +
                 "period expires. It should be acknowledged (or confirmed) instead. Defect fix is a " +
                 "separate PR; do not fix in a coverage PR.")]
    public async Task SubscriptionConfirmation_ShouldBeAcknowledged()
    {
        var harness = new SnsConsumerHarness();
        var confirmation = SnsConsumerHarness.SnsEnvelope(
            "You have chosen to subscribe to the topic arn:aws:sns:us-east-1:000000000000:otterworks-events.",
            type: "SubscriptionConfirmation");

        await harness.RunAsync(SnsConsumerHarness.Message(confirmation, "msg-confirm"));

        Assert.Equal(0, harness.Repository.RowCount);
        Assert.Single(harness.DeletedReceiptHandles);
    }

    [Fact]
    public async Task SignedEnvelopeWithNonJsonPayload_IsTreatedAsPoisonRatherThanDeadLettered()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(SnsConsumerHarness.Message(
            SnsConsumerHarness.SnsEnvelope("plain text notification"), "msg-text"));

        Assert.Equal(0, harness.Repository.RowCount);
        Assert.Empty(harness.DeletedReceiptHandles);
    }

    // ------------------------------------------------- idempotency / duplicates

    [Fact]
    public async Task SameMessageDeliveredTwice_YieldsExactlyOneAuditRow()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(
            SnsConsumerHarness.Message(RawAuditEvent, "msg-dup"),
            SnsConsumerHarness.Message(RawAuditEvent, "msg-dup"));

        Assert.Equal(2, harness.Repository.SaveCallCount);
        Assert.Equal(1, harness.Repository.RowCount);
        Assert.Equal("msg-dup", Assert.Single(harness.Repository.Rows).Id);
    }

    [Fact]
    public async Task SameMessageDeliveredAcrossTwoPolls_YieldsExactlyOneAuditRow()
    {
        var first = new SnsConsumerHarness();
        await first.RunAsync(SnsConsumerHarness.Message(RawAuditEvent, "msg-dup"));

        // A second poll cycle re-delivers the identical SQS message (at-least-once delivery).
        var again = new SnsConsumerHarness();
        again.Repository.Seed(first.Repository.Rows.ToArray());
        await again.RunAsync(SnsConsumerHarness.Message(RawAuditEvent, "msg-dup"));

        Assert.Equal(1, again.Repository.RowCount);
    }

    [Fact]
    public async Task DuplicateEventRepublishedUnderANewMessageId_CreatesASecondRow()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(
            SnsConsumerHarness.Message(RawAuditEvent, "msg-a"),
            SnsConsumerHarness.Message(RawAuditEvent, "msg-b"));

        // FINDING (audit-3): de-duplication is keyed solely on the SQS MessageId. An SNS retry or
        // a re-publish of the same logical event arrives with a new MessageId and is recorded again.
        Assert.Equal(2, harness.Repository.RowCount);
    }

    [Fact(Skip = "FINDING (audit-3): duplicate suppression relies on the SQS MessageId being reused. " +
                 "The same logical audit event republished by SNS (new MessageId) is stored twice, " +
                 "inflating compliance counts. A content/idempotency key on the event payload is needed. " +
                 "Defect fix is a separate PR; do not fix in a coverage PR.")]
    public async Task DuplicateEventRepublishedUnderANewMessageId_ShouldBeSuppressed()
    {
        var harness = new SnsConsumerHarness();

        await harness.RunAsync(
            SnsConsumerHarness.Message(RawAuditEvent, "msg-a"),
            SnsConsumerHarness.Message(RawAuditEvent, "msg-b"));

        Assert.Equal(1, harness.Repository.RowCount);
    }
}
