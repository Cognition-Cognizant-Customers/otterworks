package com.otterworks.portal.feedback;

import com.otterworks.portal.common.Json;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import software.amazon.awssdk.services.eventbridge.EventBridgeClient;
import software.amazon.awssdk.services.eventbridge.model.PutEventsRequest;
import software.amazon.awssdk.services.eventbridge.model.PutEventsRequestEntry;

/** EventBridge publisher for committed feedback submissions. */
public final class EventBridgeFeedbackEventPublisher implements FeedbackEventPublisher {

    static final UUID IDEMPOTENCY_NAMESPACE =
            UUID.fromString("6ba7b810-9dad-11d1-80b4-00c04fd430c8");

    private final EventBridgeClient client;
    private final String busName;

    public EventBridgeFeedbackEventPublisher(EventBridgeClient client, String busName) {
        this.client = client;
        this.busName = busName;
    }

    @Override
    public void publish(Feedback feedback, String namespace) {
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("idempotencyKey", idempotencyKey(feedback.getId()));
        detail.put("feedbackId", feedback.getId());
        detail.put("userId", feedback.getUserId());
        detail.put("rating", feedback.getRating());
        detail.put("message", feedback.getMessage());
        detail.put("createdAt", feedback.getCreatedAt());
        detail.put("namespace", namespace);

        var response = client.putEvents(PutEventsRequest.builder()
                .entries(PutEventsRequestEntry.builder()
                        .eventBusName(busName)
                        .source("otterworks.portal")
                        .detailType("feedback.submitted")
                        .detail(writeDetail(detail))
                        .build())
                .build());
        if (response.failedEntryCount() > 0) {
            throw new IllegalStateException("EventBridge PutEvents reported failed entries");
        }
    }

    static String idempotencyKey(long feedbackId) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-1");
            digest.update(toBytes(IDEMPOTENCY_NAMESPACE));
            digest.update(("feedback:" + feedbackId).getBytes(StandardCharsets.UTF_8));
            byte[] hash = digest.digest();
            hash[6] &= 0x0f;
            hash[6] |= 0x50;
            hash[8] &= 0x3f;
            hash[8] |= 0x80;
            long most = 0;
            long least = 0;
            for (int i = 0; i < 8; i++) {
                most = (most << 8) | (hash[i] & 0xffL);
                least = (least << 8) | (hash[8 + i] & 0xffL);
            }
            return new UUID(most, least).toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-1 is unavailable", e);
        }
    }

    private static String writeDetail(Map<String, Object> detail) {
        try {
            return Json.MAPPER.writeValueAsString(detail);
        } catch (Exception e) {
            throw new IllegalStateException("Could not serialize feedback event", e);
        }
    }

    private static byte[] toBytes(UUID value) {
        byte[] bytes = new byte[16];
        long most = value.getMostSignificantBits();
        long least = value.getLeastSignificantBits();
        for (int i = 0; i < 8; i++) {
            bytes[i] = (byte) (most >>> (56 - i * 8));
            bytes[8 + i] = (byte) (least >>> (56 - i * 8));
        }
        return bytes;
    }
}
