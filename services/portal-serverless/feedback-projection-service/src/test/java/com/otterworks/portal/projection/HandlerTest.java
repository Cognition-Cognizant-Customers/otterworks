package com.otterworks.portal.projection;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.amazonaws.services.lambda.runtime.events.SQSBatchResponse;
import com.amazonaws.services.lambda.runtime.events.SQSEvent;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

class HandlerTest {

    /** In-memory projection with the same dedupe semantics as DynamoStatsStore. */
    static class InMemoryStats implements StatsStore {
        final Map<String, Integer> applied = new LinkedHashMap<>();
        int cnt;
        int ratingSum;
        boolean failNext;

        @Override
        public boolean apply(FeedbackSubmittedEvent event) {
            if (failNext) {
                failNext = false;
                throw new RuntimeException("simulated transient store failure");
            }
            if (applied.containsKey(event.eventId)) {
                return false;
            }
            applied.put(event.eventId, event.rating);
            cnt++;
            ratingSum += event.rating;
            return true;
        }
    }

    private static String body(String eventId, long feedbackId, String userId, int rating) {
        return "{\"detail-type\":\"FeedbackSubmitted\",\"source\":\"otterworks.portal.feedback\","
                + "\"detail\":{\"eventId\":\"" + eventId + "\",\"feedbackId\":" + feedbackId
                + ",\"userId\":\"" + userId + "\",\"rating\":" + rating
                + ",\"message\":\"m\",\"createdAt\":\"2026-08-18T00:00:00Z\"}}";
    }

    private static SQSEvent sqs(String... bodies) {
        List<SQSEvent.SQSMessage> records = new ArrayList<>();
        int i = 0;
        for (String b : bodies) {
            SQSEvent.SQSMessage m = new SQSEvent.SQSMessage();
            m.setMessageId("msg-" + (++i));
            m.setBody(b);
            records.add(m);
        }
        SQSEvent event = new SQSEvent();
        event.setRecords(records);
        return event;
    }

    private static List<String> failedIds(SQSBatchResponse response) {
        return response.getBatchItemFailures().stream()
                .map(SQSBatchResponse.BatchItemFailure::getItemIdentifier)
                .collect(Collectors.toList());
    }

    @Test
    void appliesBatchAndConverges() {
        InMemoryStats stats = new InMemoryStats();
        Handler handler = new Handler(stats);
        SQSBatchResponse response = handler.handleRequest(
                sqs(body("e1", 1, "otto", 4), body("e2", 2, "pearl", 5)), null);
        assertTrue(failedIds(response).isEmpty());
        assertEquals(2, stats.cnt);
        assertEquals(9, stats.ratingSum);
    }

    @Test
    void duplicateDeliveryIsANoOp() {
        InMemoryStats stats = new InMemoryStats();
        Handler handler = new Handler(stats);
        handler.handleRequest(sqs(body("e1", 1, "otto", 4)), null);
        SQSBatchResponse response = handler.handleRequest(sqs(body("e1", 1, "otto", 4)), null);
        assertTrue(failedIds(response).isEmpty());
        assertEquals(1, stats.cnt);
        assertEquals(4, stats.ratingSum);
    }

    @Test
    void partialBatchFailureReportsOnlyTheFailedMessage() {
        InMemoryStats stats = new InMemoryStats();
        Handler handler = new Handler(stats);
        SQSBatchResponse response = handler.handleRequest(
                sqs(body("e1", 1, "otto", 4), "{not json", body("e3", 3, "pearl", 2)), null);
        assertEquals(List.of("msg-2"), failedIds(response));
        assertEquals(2, stats.cnt);
        assertEquals(6, stats.ratingSum);
    }

    @Test
    void poisonClassification() {
        assertThrows(PoisonMessageException.class,
                () -> FeedbackSubmittedEvent.parse("{not json"));
        assertThrows(PoisonMessageException.class,
                () -> FeedbackSubmittedEvent.parse("{\"detail\":{}}"));
        assertThrows(PoisonMessageException.class,
                () -> FeedbackSubmittedEvent.parse(body("", 1, "otto", 4)));
        assertThrows(PoisonMessageException.class,
                () -> FeedbackSubmittedEvent.parse(body("e1", 1, "", 4)));
        assertThrows(PoisonMessageException.class,
                () -> FeedbackSubmittedEvent.parse(body("e1", 1, "otto", 6)));
        assertThrows(PoisonMessageException.class,
                () -> FeedbackSubmittedEvent.parse(body("e1", 1, "otto", 0)));
        // Unknown extra fields are tolerated (non-strict binding).
        FeedbackSubmittedEvent ok = FeedbackSubmittedEvent.parse(
                "{\"detail\":{\"eventId\":\"e1\",\"feedbackId\":9,\"userId\":\"otto\","
                        + "\"rating\":3,\"surprise\":true}}");
        assertEquals(9, ok.feedbackId);
        assertEquals(3, ok.rating);
    }

    @Test
    void transientFailureIsRetriedViaBatchItemFailure() {
        InMemoryStats stats = new InMemoryStats();
        Handler handler = new Handler(stats);
        stats.failNext = true;
        SQSBatchResponse first = handler.handleRequest(sqs(body("e1", 1, "otto", 4)), null);
        assertEquals(List.of("msg-1"), failedIds(first));
        SQSBatchResponse retry = handler.handleRequest(sqs(body("e1", 1, "otto", 4)), null);
        assertTrue(failedIds(retry).isEmpty());
        assertEquals(1, stats.cnt);
    }

    @Test
    void emptyBatchIsSilent() {
        Handler handler = new Handler(new InMemoryStats());
        SQSBatchResponse response = handler.handleRequest(sqs(), null);
        assertTrue(failedIds(response).isEmpty());
        assertTrue(handler.handleRequest(null, null).getBatchItemFailures().isEmpty());
    }
}
