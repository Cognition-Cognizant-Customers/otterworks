package com.otterworks.portal.feedback;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

import java.util.UUID;
import org.junit.jupiter.api.Test;

class EventBridgeFeedbackEventPublisherTest {

    @Test
    void idempotencyKeyIsDeterministicUuid5() {
        String key = EventBridgeFeedbackEventPublisher.idempotencyKey(42);
        assertEquals(key, EventBridgeFeedbackEventPublisher.idempotencyKey(42));
        assertNotEquals(
                key,
                EventBridgeFeedbackEventPublisher.idempotencyKey(43));
        assertEquals(5, UUID.fromString(key).version());
    }
}
