using System.Text.Json;
using System.Text.Json.Serialization;
using Amazon.SimpleNotificationService;
using Amazon.SimpleNotificationService.Model;
using Amazon.SQS;
using Amazon.SQS.Model;
using Microsoft.Extensions.Options;
using OtterWorks.AuditService.Config;

namespace OtterWorks.AuditService.Services;

public class SnsConsumer : BackgroundService
{
    private readonly IAmazonSQS _sqsClient;
    private readonly IAuditRepository _repository;
    private readonly AwsSettings _settings;
    private readonly ILogger<SnsConsumer> _logger;
    private string? _queueUrl;

    public SnsConsumer(
        IAmazonSQS sqsClient,
        IAuditRepository repository,
        IOptions<AwsSettings> settings,
        ILogger<SnsConsumer> logger)
    {
        _sqsClient = sqsClient;
        _repository = repository;
        _settings = settings.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _logger.LogInformation("SNS Consumer starting, waiting for audit events...");

        try
        {
            _queueUrl = await GetOrCreateQueueUrlAsync(stoppingToken);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to initialize SQS queue. SNS Consumer will not process messages");
            return;
        }

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var receiveRequest = new ReceiveMessageRequest
                {
                    QueueUrl = _queueUrl,
                    MaxNumberOfMessages = 10,
                    WaitTimeSeconds = 20,
                };

                var response = await _sqsClient.ReceiveMessageAsync(receiveRequest, stoppingToken);

                foreach (var message in response.Messages ?? [])
                {
                    await ProcessMessageAsync(message, stoppingToken);
                }
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Error processing SQS messages");
                await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
            }
        }

        _logger.LogInformation("SNS Consumer stopping");
    }

    private async Task<string> GetOrCreateQueueUrlAsync(CancellationToken ct)
    {
        const string queueName = "otterworks-audit-events-queue";
        try
        {
            var response = await _sqsClient.GetQueueUrlAsync(queueName, ct);
            return response.QueueUrl;
        }
        catch (QueueDoesNotExistException)
        {
            var createResponse = await _sqsClient.CreateQueueAsync(new CreateQueueRequest
            {
                QueueName = queueName,
            }, ct);
            return createResponse.QueueUrl;
        }
    }

    private async Task ProcessMessageAsync(Message message, CancellationToken ct)
    {
        try
        {
            var snsMessage = TryParseSnsEnvelope(message.Body);
            var eventBody = snsMessage ?? message.Body;

            var fileEvent = JsonSerializer.Deserialize<FileEventMessage>(eventBody, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
            });
            if (fileEvent?.EventType == "file_shared")
            {
                var fileShareEvent = new AuditEvent
                {
                    Id = message.MessageId,
                    UserId = fileEvent.OwnerId ?? "system",
                    Action = "share",
                    ResourceType = "file",
                    ResourceId = fileEvent.FileId ?? string.Empty,
                    Details = new Dictionary<string, string>
                    {
                        ["sharedWithUserId"] = fileEvent.SharedWithUserId ?? string.Empty,
                    },
                    Timestamp = fileEvent.Timestamp ?? DateTime.UtcNow,
                };

                await _repository.SaveEventAsync(fileShareEvent);
                _logger.LogDebug("Processed file share SNS event for {FileId}", fileEvent.FileId);
                await _sqsClient.DeleteMessageAsync(_queueUrl, message.ReceiptHandle, ct);
                return;
            }

            var auditEvent = JsonSerializer.Deserialize<AuditEventMessage>(eventBody, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
            });

            if (auditEvent is null)
            {
                _logger.LogWarning("Failed to deserialize audit event from message {MessageId}", message.MessageId);
                await _sqsClient.DeleteMessageAsync(_queueUrl, message.ReceiptHandle, ct);
                return;
            }

            var entity = new AuditEvent
            {
                Id = message.MessageId,
                UserId = auditEvent.UserId ?? "system",
                Action = auditEvent.Action ?? "unknown",
                ResourceType = auditEvent.ResourceType ?? "unknown",
                ResourceId = auditEvent.ResourceId ?? string.Empty,
                Details = auditEvent.Details,
                IpAddress = auditEvent.IpAddress,
                UserAgent = auditEvent.UserAgent,
                Timestamp = auditEvent.Timestamp ?? DateTime.UtcNow,
            };

            await _repository.SaveEventAsync(entity);
            _logger.LogDebug("Processed SNS event: {Action} on {ResourceType}/{ResourceId}",
                entity.Action, entity.ResourceType, entity.ResourceId);

            await _sqsClient.DeleteMessageAsync(_queueUrl, message.ReceiptHandle, ct);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to process message {MessageId}", message.MessageId);
        }
    }

    private static string? TryParseSnsEnvelope(string body)
    {
        try
        {
            using var doc = JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("Message", out var messageElement))
            {
                return messageElement.GetString();
            }
        }
        catch (JsonException)
        {
            // Not an SNS envelope
        }

        return null;
    }

    private sealed class AuditEventMessage
    {
        public string? UserId { get; set; }
        public string? Action { get; set; }
        public string? ResourceType { get; set; }
        public string? ResourceId { get; set; }
        public Dictionary<string, string>? Details { get; set; }
        public string? IpAddress { get; set; }
        public string? UserAgent { get; set; }
        public DateTime? Timestamp { get; set; }
    }

    private sealed class FileEventMessage
    {
        [JsonPropertyName("eventType")]
        public string? EventType { get; set; }

        [JsonPropertyName("fileId")]
        public string? FileId { get; set; }

        [JsonPropertyName("ownerId")]
        public string? OwnerId { get; set; }

        [JsonPropertyName("sharedWithUserId")]
        public string? SharedWithUserId { get; set; }

        [JsonPropertyName("timestamp")]
        public DateTime? Timestamp { get; set; }
    }
}
