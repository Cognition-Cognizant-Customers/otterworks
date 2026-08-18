package com.otterworks.portal.projection;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.otterworks.portal.common.Json;

/**
 * Parsed FeedbackSubmitted event as delivered to SQS: the EventBridge envelope's
 * {@code detail} field carries the domain payload. Validation implements the
 * contract's poison policy; unknown fields are tolerated.
 */
public final class FeedbackSubmittedEvent {

    static final int MIN_RATING = 1;
    static final int MAX_RATING = 5;

    public final String eventId;
    public final long feedbackId;
    public final String userId;
    public final int rating;

    private FeedbackSubmittedEvent(String eventId, long feedbackId, String userId, int rating) {
        this.eventId = eventId;
        this.feedbackId = feedbackId;
        this.userId = userId;
        this.rating = rating;
    }

    /** Parses an SQS body (EventBridge envelope JSON); throws PoisonMessageException. */
    public static FeedbackSubmittedEvent parse(String body) {
        JsonNode root;
        try {
            root = Json.MAPPER.readTree(body == null ? "" : body);
        } catch (JsonProcessingException e) {
            throw new PoisonMessageException("malformed JSON body", e);
        }
        if (root == null || !root.isObject()) {
            throw new PoisonMessageException("body is not a JSON object");
        }
        JsonNode detail = root.path("detail");
        if (!detail.isObject()) {
            throw new PoisonMessageException("missing event detail");
        }
        String eventId = requireText(detail, "eventId");
        String userId = requireText(detail, "userId");
        JsonNode feedbackId = detail.path("feedbackId");
        if (!feedbackId.canConvertToLong()) {
            throw new PoisonMessageException("missing or non-numeric feedbackId");
        }
        JsonNode rating = detail.path("rating");
        if (!rating.canConvertToInt()
                || rating.asInt() < MIN_RATING || rating.asInt() > MAX_RATING) {
            throw new PoisonMessageException("rating outside " + MIN_RATING + "-" + MAX_RATING);
        }
        return new FeedbackSubmittedEvent(eventId, feedbackId.asLong(), userId, rating.asInt());
    }

    private static String requireText(JsonNode detail, String field) {
        JsonNode node = detail.path(field);
        if (!node.isTextual() || node.asText().isBlank()) {
            throw new PoisonMessageException("missing or blank " + field);
        }
        return node.asText();
    }
}
