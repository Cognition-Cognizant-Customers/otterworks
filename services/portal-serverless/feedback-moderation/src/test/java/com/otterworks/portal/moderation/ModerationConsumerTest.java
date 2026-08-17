package com.otterworks.portal.moderation;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class ModerationConsumerTest {

    private final RecordingStore store = new RecordingStore();
    private final ModerationConsumer consumer = new ModerationConsumer(store, new ObjectMapper());

    @Test
    void approvesCleanMessage() {
        assertEquals(true, consumer.process(event("key-1", 10, "alice", 5, "hello there")));
        assertEquals("approved", store.records.get(0).status());
    }

    @Test
    void flagsBannedTerm() {
        assertEquals(true, consumer.process(event("key-2", 11, "alice", 2, "this is spam")));
        assertEquals("flagged", store.records.get(0).status());
    }

    @Test
    void duplicateDeliveryIsNoOp() {
        String body = event("key-3", 12, "alice", 4, "hello");
        assertEquals(true, consumer.process(body));
        assertEquals(false, consumer.process(body));
        assertEquals(1, store.records.size());
    }

    @Test
    void rejectsEachPoisonFieldAndInvalidRating() {
        assertPoison(eventWithout("idempotencyKey"));
        assertPoison(eventWithout("feedbackId"));
        assertPoison(eventWithout("userId"));
        assertPoison(eventWithout("message"));
        assertPoison(event("key-4", 13, "alice", 0, "hello"));
        assertPoison(event("key-5", 14, "alice", 6, "hello"));
    }

    private void assertPoison(String body) {
        assertThrows(ModerationConsumer.PoisonMessageException.class, () -> consumer.process(body));
    }

    private static String event(String key, long id, String user, int rating, String message) {
        return "{\"detail\":{\"idempotencyKey\":\"" + key + "\",\"feedbackId\":" + id
                + ",\"userId\":\"" + user + "\",\"rating\":" + rating
                + ",\"message\":\"" + message + "\"}}";
    }

    private static String eventWithout(String field) {
        String body = event("key-poison", 15, "alice", 3, "hello");
        return body.replaceFirst("\"" + field + "\":[^,}]+,?", "");
    }

    private static final class RecordingStore implements ModerationStore {
        private final List<ModerationRecord> records = new ArrayList<>();

        @Override
        public boolean putIfAbsent(ModerationRecord record) {
            if (records.stream().anyMatch(existing ->
                    existing.idempotencyKey().equals(record.idempotencyKey()))) {
                return false;
            }
            records.add(record);
            return true;
        }
    }
}
