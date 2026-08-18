package com.otterworks.portal.projection;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestHandler;
import com.amazonaws.services.lambda.runtime.events.SQSBatchResponse;
import com.amazonaws.services.lambda.runtime.events.SQSEvent;
import java.util.ArrayList;
import java.util.List;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/**
 * SQS consumer for FeedbackSubmitted events (ReportBatchItemFailures mode).
 *
 * <p>Per the unit contract: each record is applied idempotently to the
 * feedback-stats projection; a failed record (poison or transient) is returned
 * as a batch item failure so only it is retried, and the queue's redrive policy
 * delivers it to the DLQ with full payload once maxReceiveCount is exhausted.
 * An empty/unparseable batch yields an empty response — no phantom work.
 */
public class Handler implements RequestHandler<SQSEvent, SQSBatchResponse> {

    private final StatsStore store;

    public Handler() {
        this(new DynamoStatsStore(DynamoDbClient.create(), System.getenv("STATS_TABLE_NAME")));
    }

    public Handler(StatsStore store) {
        this.store = store;
    }

    @Override
    public SQSBatchResponse handleRequest(SQSEvent event, Context context) {
        List<SQSBatchResponse.BatchItemFailure> failures = new ArrayList<>();
        List<SQSEvent.SQSMessage> records =
                event == null || event.getRecords() == null ? List.of() : event.getRecords();
        for (SQSEvent.SQSMessage record : records) {
            try {
                FeedbackSubmittedEvent parsed = FeedbackSubmittedEvent.parse(record.getBody());
                boolean applied = store.apply(parsed);
                if (!applied) {
                    System.out.println("duplicate event " + parsed.eventId + " skipped");
                }
            } catch (PoisonMessageException e) {
                System.err.println("poison message " + record.getMessageId() + ": " + e.getMessage());
                failures.add(SQSBatchResponse.BatchItemFailure.builder()
                        .withItemIdentifier(record.getMessageId()).build());
            } catch (RuntimeException e) {
                System.err.println("transient failure on " + record.getMessageId() + ": " + e);
                failures.add(SQSBatchResponse.BatchItemFailure.builder()
                        .withItemIdentifier(record.getMessageId()).build());
            }
        }
        return SQSBatchResponse.builder().withBatchItemFailures(failures).build();
    }
}
