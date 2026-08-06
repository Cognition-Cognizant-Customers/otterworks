using Amazon.SQS;
using Amazon.SQS.Model;
using Microsoft.Extensions.Options;
using Moq;
using OtterWorks.AuditService.Config;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests.Support;

/// <summary>
/// Drives <see cref="SnsConsumer"/> through its real <see cref="Microsoft.Extensions.Hosting.BackgroundService"/>
/// lifecycle against a fake SQS client. The first receive returns the supplied batch; every later
/// receive parks on the stopping token, so the harness can wait on a completion signal instead of
/// sleeping — the batch is guaranteed to be fully processed before the signal fires.
/// </summary>
public sealed class SnsConsumerHarness
{
    public const string QueueUrl = "https://sqs.test/otterworks-audit-events-queue";

    private readonly TaskCompletionSource _batchProcessed =
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private int _receiveCount;

    public SnsConsumerHarness()
    {
        Sqs = new Mock<IAmazonSQS>(MockBehavior.Strict);
        Repository = new InMemoryAuditRepository();
        Logger = new CapturingLogger<SnsConsumer>();

        Sqs.Setup(s => s.GetQueueUrlAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new GetQueueUrlResponse { QueueUrl = QueueUrl });

        Sqs.Setup(s => s.DeleteMessageAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string queueUrl, string receipt, CancellationToken _) =>
            {
                DeletedReceiptHandles.Add((queueUrl, receipt));
                return new DeleteMessageResponse();
            });

        Sqs.Setup(s => s.Dispose());
    }

    public Mock<IAmazonSQS> Sqs { get; }

    public InMemoryAuditRepository Repository { get; }

    public CapturingLogger<SnsConsumer> Logger { get; }

    public List<(string QueueUrl, string ReceiptHandle)> DeletedReceiptHandles { get; } = new();

    public AwsSettings Settings { get; } = new()
    {
        Region = "us-east-1",
        DynamoDbTable = "test-audit-events",
        S3ArchiveBucket = "test-archive-bucket",
    };

    public static Message Message(string body, string messageId = "msg-1") => new()
    {
        MessageId = messageId,
        ReceiptHandle = $"receipt-for-{messageId}",
        Body = body,
    };

    /// <summary>Wraps a payload in the SNS-to-SQS notification envelope.</summary>
    public static string SnsEnvelope(string innerMessage, string type = "Notification") =>
        $$"""
          {
            "Type": "{{type}}",
            "MessageId": "11111111-2222-3333-4444-555555555555",
            "TopicArn": "arn:aws:sns:us-east-1:000000000000:otterworks-events",
            "Message": {{System.Text.Json.JsonSerializer.Serialize(innerMessage)}},
            "Timestamp": "2026-01-01T00:00:00.000Z",
            "SignatureVersion": "1",
            "Signature": "ZmFrZS1zaWduYXR1cmU=",
            "SigningCertURL": "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-fake.pem"
          }
          """;

    /// <summary>Runs the consumer over one batch of messages and returns once it has stopped.</summary>
    public async Task RunAsync(params Message[] messages)
    {
        SetupReceive(_ => new ReceiveMessageResponse { Messages = messages.ToList() });
        await RunUntilSettledAsync();
    }

    /// <summary>Runs the consumer where the first receive call throws.</summary>
    public async Task RunWithReceiveFailureAsync(Exception failure)
    {
        SetupReceive(_ =>
        {
            // Signal before throwing: the consumer's error path backs off before polling again,
            // and the harness must not wait out that back-off.
            _batchProcessed.TrySetResult();
            throw failure;
        });
        await RunUntilSettledAsync();
    }

    /// <summary>Runs the consumer with a queue-lookup outcome supplied by the caller.</summary>
    public async Task RunWithQueueLookupAsync(Func<GetQueueUrlResponse> queueLookup, bool createQueue = false)
    {
        Sqs.Setup(s => s.GetQueueUrlAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(queueLookup);

        if (createQueue)
        {
            Sqs.Setup(s => s.CreateQueueAsync(It.IsAny<CreateQueueRequest>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(new CreateQueueResponse { QueueUrl = QueueUrl });
        }

        SetupReceive(_ => new ReceiveMessageResponse { Messages = new List<Message>() });
        await RunUntilSettledAsync();
    }

    private void SetupReceive(Func<ReceiveMessageRequest, ReceiveMessageResponse> firstBatch)
    {
        Sqs.Setup(s => s.ReceiveMessageAsync(It.IsAny<ReceiveMessageRequest>(), It.IsAny<CancellationToken>()))
            .Returns(async (ReceiveMessageRequest request, CancellationToken ct) =>
            {
                if (Interlocked.Increment(ref _receiveCount) == 1)
                {
                    return firstBatch(request);
                }

                // Every subsequent poll parks until the service is stopped, mirroring long polling
                // on an empty queue without introducing a timing dependency.
                _batchProcessed.TrySetResult();
                await Task.Delay(Timeout.Infinite, ct);
                return new ReceiveMessageResponse();
            });
    }

    private async Task RunUntilSettledAsync()
    {
        var consumer = new SnsConsumer(Sqs.Object, Repository, Options.Create(Settings), Logger);
        await consumer.StartAsync(CancellationToken.None);

        var executing = consumer.ExecuteTask ?? Task.CompletedTask;
        await Task.WhenAny(_batchProcessed.Task, executing);

        await consumer.StopAsync(CancellationToken.None);
        consumer.Dispose();

        if (executing.IsFaulted)
        {
            _ = executing.Exception;
        }
    }
}
