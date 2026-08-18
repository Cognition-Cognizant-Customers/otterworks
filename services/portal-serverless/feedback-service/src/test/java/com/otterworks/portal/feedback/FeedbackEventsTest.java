package com.otterworks.portal.feedback;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

class FeedbackEventsTest {

    @Test
    void eventIdIsRfc4122Uuid5AndDeterministic() {
        // Pinned against python uuid.uuid5(NAMESPACE, "feedback:<id>").
        assertEquals("599c000c-c3f3-5c46-bf20-054e793ad223",
                FeedbackEvents.eventId(1).toString());
        assertEquals("2985cd03-76dc-5124-b6c3-74c576712b42",
                FeedbackEvents.eventId(2).toString());
        assertEquals("5f20fe56-d72c-5c4b-b63c-fd57f12d70a6",
                FeedbackEvents.eventId(42).toString());
        // Same input, same id: replay-safe identity.
        assertEquals(FeedbackEvents.eventId(42), FeedbackEvents.eventId(42));
        assertEquals(5, FeedbackEvents.eventId(7).version());
        assertEquals(2, FeedbackEvents.eventId(7).variant());
    }

    static class RecordingStore implements FeedbackStore {
        final List<Feedback> items = new ArrayList<>();
        final AtomicLong seq = new AtomicLong();

        public long nextId() {
            return seq.incrementAndGet();
        }

        public void put(Feedback f) {
            items.add(f);
        }

        public List<Feedback> findByUserId(String userId) {
            return items.stream()
                    .filter(f -> f.getUserId().equals(userId))
                    .collect(Collectors.toList());
        }

        public List<Feedback> findAll() {
            return new ArrayList<>(items);
        }
    }

    @Test
    void submitPublishesAfterCommitWithCommittedState() {
        RecordingStore store = new RecordingStore();
        List<Feedback> published = new ArrayList<>();
        FeedbackService service = new FeedbackService(store, feedback -> {
            // Write-then-publish: the row is already committed when publish runs.
            assertEquals(1, store.items.size());
            published.add(feedback);
        });

        Feedback result = service.submit("otto", 4, "great portal");

        assertEquals(1, published.size());
        assertEquals(result.getId(), published.get(0).getId());
        assertEquals(4, published.get(0).getRating());
    }

    @Test
    void rejectedSubmissionPublishesNothing() {
        RecordingStore store = new RecordingStore();
        List<Feedback> published = new ArrayList<>();
        FeedbackService service = new FeedbackService(store, published::add);
        try {
            service.submit("otto", 6, "too enthusiastic");
        } catch (RuntimeException expected) {
            // 400 per the synchronous contract
        }
        assertEquals(0, published.size());
        assertEquals(0, store.items.size());
    }

    @Test
    void publishFailureDoesNotFailTheCommittedRequest() {
        RecordingStore store = new RecordingStore();
        FeedbackService service = new FeedbackService(store, feedback -> {
            throw new RuntimeException("bus unavailable");
        });

        Feedback result = service.submit("otto", 5, "still counts");

        assertEquals(1, store.items.size());
        assertEquals("otto", result.getUserId());
        assertEquals(Instant.class, store.items.get(0).getCreatedAt().getClass());
    }
}
