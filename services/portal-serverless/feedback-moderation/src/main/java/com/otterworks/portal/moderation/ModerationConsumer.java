package com.otterworks.portal.moderation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import java.time.Instant;
import java.util.Locale;
import java.util.Set;

public final class ModerationConsumer {

    private static final Set<String> BANNED_TERMS = Set.of("spam", "scam", "abuse");

    private final ModerationStore store;
    private final ObjectMapper mapper;

    public ModerationConsumer(ModerationStore store, ObjectMapper mapper) {
        this.store = store;
        this.mapper = mapper;
    }

    public boolean process(String body) {
        try {
            JsonNode event = mapper.readTree(body);
            JsonNode detail = event.has("detail") && event.get("detail").isTextual()
                    ? mapper.readTree(event.get("detail").asText())
                    : event.get("detail");
            if (detail == null || detail.isNull()) {
                throw poison("detail is required");
            }
            String idempotencyKey = requiredText(detail, "idempotencyKey");
            long feedbackId = requiredLong(detail, "feedbackId");
            String userId = requiredText(detail, "userId");
            String message = requiredText(detail, "message");
            int rating = requiredInt(detail, "rating");
            if (rating < 1 || rating > 5) {
                throw poison("rating must be between 1 and 5");
            }
            String normalized = message.toLowerCase(Locale.ROOT);
            String matched = BANNED_TERMS.stream()
                    .filter(normalized::contains)
                    .findFirst()
                    .orElse(null);
            String status = matched == null ? "approved" : "flagged";
            String reason = matched == null ? "no banned terms" : "matched banned term: " + matched;
            return store.putIfAbsent(new ModerationRecord(
                    idempotencyKey, feedbackId, userId, status, reason, Instant.now()));
        } catch (PoisonMessageException e) {
            throw e;
        } catch (JsonProcessingException e) {
            throw poison("malformed moderation event");
        } catch (Exception e) {
            throw new IllegalStateException("invalid moderation event", e);
        }
    }

    private static String requiredText(JsonNode detail, String field) {
        JsonNode node = detail.get(field);
        if (node == null || (!node.isTextual() && !node.isNumber()) || node.asText().isBlank()) {
            throw poison(field + " is required");
        }
        return node.asText();
    }

    private static long requiredLong(JsonNode detail, String field) {
        JsonNode node = detail.get(field);
        if (node == null || !node.canConvertToLong()) {
            throw poison(field + " is required");
        }
        return node.asLong();
    }

    private static int requiredInt(JsonNode detail, String field) {
        JsonNode node = detail.get(field);
        if (node == null || !node.canConvertToInt()) {
            throw poison(field + " is required");
        }
        return node.asInt();
    }

    private static PoisonMessageException poison(String message) {
        return new PoisonMessageException(message);
    }

    public static final class PoisonMessageException extends RuntimeException {
        public PoisonMessageException(String message) {
            super(message);
        }
    }
}
