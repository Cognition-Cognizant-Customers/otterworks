import com.amazonaws.services.lambda.runtime.events.SQSBatchResponse;
import com.amazonaws.services.lambda.runtime.events.SQSEvent;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.DeleteMessageBatchRequest;
import software.amazon.awssdk.services.sqs.model.DeleteMessageBatchRequestEntry;
import software.amazon.awssdk.services.sqs.model.Message;
import software.amazon.awssdk.services.sqs.model.ReceiveMessageRequest;

/**
 * Fixture stand-in for the Lambda SQS event-source mapping: long-polls the
 * feedback events queue in batches of 5 (no batching window), invokes the real
 * projection Handler, and — mirroring ReportBatchItemFailures — deletes only
 * the records the handler did not report as failures. Failed records become
 * visible again and LocalStack's redrive policy moves them to the DLQ once
 * maxReceiveCount is exhausted. No live AWS resources are involved.
 *
 * Env: QUEUE_URL (required), STATS_TABLE_NAME (required),
 *      DYNAMO_ENDPOINT / SQS_ENDPOINT (default http://localhost:4570),
 *      PUMP_STATS_FILE (optional JSON counters for the recon script),
 *      OUTAGE_FILE (optional chaos switch: while the file exists, every apply
 *      throws a transient failure — the recon's simulated downstream outage).
 */
public final class AsyncFixturePump {

    public static void main(String[] args) throws Exception {
        String endpoint = env("DYNAMO_ENDPOINT", "http://localhost:4570");
        String sqsEndpoint = env("SQS_ENDPOINT", endpoint);
        String queueUrl = System.getenv("QUEUE_URL");
        String statsTable = System.getenv("STATS_TABLE_NAME");
        String statsFile = System.getenv("PUMP_STATS_FILE");

        StaticCredentialsProvider credentials = StaticCredentialsProvider.create(
                AwsBasicCredentials.create("test", "test"));
        DynamoDbClient dynamo = DynamoDbClient.builder()
                .endpointOverride(URI.create(endpoint))
                .region(Region.US_EAST_1)
                .credentialsProvider(credentials)
                .build();
        SqsClient sqs = SqsClient.builder()
                .endpointOverride(URI.create(sqsEndpoint))
                .region(Region.US_EAST_1)
                .credentialsProvider(credentials)
                .build();

        String outageFile = System.getenv("OUTAGE_FILE");
        com.otterworks.portal.projection.StatsStore store =
                new com.otterworks.portal.projection.DynamoStatsStore(dynamo, statsTable);
        com.otterworks.portal.projection.StatsStore chaosStore = event -> {
            if (outageFile != null && !outageFile.isBlank()
                    && Files.exists(Path.of(outageFile))) {
                throw new RuntimeException("injected transient outage (" + outageFile + ")");
            }
            return store.apply(event);
        };
        var handler = new com.otterworks.portal.projection.Handler(chaosStore);

        long invocations = 0;
        long processed = 0;
        long reportedFailures = 0;
        long crashes = 0;
        System.out.println("async fixture pump polling " + queueUrl);
        while (true) {
            List<Message> messages = sqs.receiveMessage(ReceiveMessageRequest.builder()
                    .queueUrl(queueUrl)
                    .maxNumberOfMessages(5)
                    .waitTimeSeconds(1)
                    .build()).messages();
            if (messages.isEmpty()) {
                writeStats(statsFile, invocations, processed, reportedFailures, crashes);
                continue;
            }

            SQSEvent event = new SQSEvent();
            List<SQSEvent.SQSMessage> records = new ArrayList<>();
            for (Message message : messages) {
                SQSEvent.SQSMessage record = new SQSEvent.SQSMessage();
                record.setMessageId(message.messageId());
                record.setBody(message.body());
                record.setReceiptHandle(message.receiptHandle());
                records.add(record);
            }
            event.setRecords(records);

            Set<String> failedIds = new HashSet<>();
            invocations++;
            try {
                SQSBatchResponse response = handler.handleRequest(event, null);
                for (SQSBatchResponse.BatchItemFailure failure : response.getBatchItemFailures()) {
                    failedIds.add(failure.getItemIdentifier());
                }
            } catch (RuntimeException e) {
                // A crashed invocation retries the whole batch, like the real mapping.
                crashes++;
                System.err.println("pump invocation crashed: " + e);
                continue;
            }

            List<DeleteMessageBatchRequestEntry> deletes = new ArrayList<>();
            for (Message message : messages) {
                if (failedIds.contains(message.messageId())) {
                    reportedFailures++;
                } else {
                    processed++;
                    deletes.add(DeleteMessageBatchRequestEntry.builder()
                            .id(message.messageId())
                            .receiptHandle(message.receiptHandle())
                            .build());
                }
            }
            if (!deletes.isEmpty()) {
                sqs.deleteMessageBatch(DeleteMessageBatchRequest.builder()
                        .queueUrl(queueUrl)
                        .entries(deletes)
                        .build());
            }
            writeStats(statsFile, invocations, processed, reportedFailures, crashes);
        }
    }

    private static void writeStats(String file, long invocations, long processed,
            long reportedFailures, long crashes) throws Exception {
        if (file == null || file.isBlank()) {
            return;
        }
        String json = "{\"invocations\":" + invocations + ",\"processed\":" + processed
                + ",\"reported_failures\":" + reportedFailures
                + ",\"crashed_invocations\":" + crashes + "}";
        // Atomic: the recon script reads this file concurrently and must never
        // observe a truncated half-write.
        Path target = Path.of(file);
        Path temp = target.resolveSibling(target.getFileName() + ".tmp");
        Files.write(temp, json.getBytes(StandardCharsets.UTF_8));
        Files.move(temp, target, StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING);
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }
}
