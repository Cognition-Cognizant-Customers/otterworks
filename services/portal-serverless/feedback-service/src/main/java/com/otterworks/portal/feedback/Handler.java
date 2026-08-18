package com.otterworks.portal.feedback;

import com.otterworks.portal.common.ApiException;
import com.otterworks.portal.common.ApiHandler;
import java.util.LinkedHashMap;
import java.util.Map;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.eventbridge.EventBridgeClient;

/**
 * Lambda entry point for the feedback bounded context.
 *
 * <p>Routes (parity with the monolith's FeedbackController):
 * <ul>
 *   <li>POST /api/feedback</li>
 *   <li>GET  /api/feedback?userId=...</li>
 *   <li>GET  /api/feedback/average-rating</li>
 * </ul>
 */
public class Handler extends ApiHandler {

    private final FeedbackService service;

    public Handler() {
        this(new FeedbackService(
                new DynamoFeedbackStore(DynamoDbClient.create(), System.getenv("TABLE_NAME")),
                publisherFromEnv()));
    }

    /** Bus-less estates (unit tests, plain fixture) fall back to the no-op publisher. */
    private static EventPublisher publisherFromEnv() {
        String busName = System.getenv("EVENT_BUS_NAME");
        if (busName == null || busName.isBlank()) {
            return EventPublisher.NONE;
        }
        return new EventBridgePublisher(EventBridgeClient.create(), busName);
    }

    public Handler(FeedbackService service) {
        this.service = service;
    }

    @Override
    protected Result route(String method, String path, Map<String, String> query, String body) {
        String rest = subPath(path);
        if (rest == null) {
            if ("POST".equals(method)) {
                SubmitRequest request = parseBody(body, SubmitRequest.class);
                requireText(request.userId, "userId", 100);
                requireText(request.message, "message", 2000);
                return new Result(201, service.submit(request.userId, request.rating, request.message));
            }
            if ("GET".equals(method)) {
                String userId = query.get("userId");
                if (userId == null) {
                    throw ApiException.badRequest("userId query parameter is required");
                }
                return new Result(200, service.listForUser(userId));
            }
        } else if ("average-rating".equals(rest) && "GET".equals(method)) {
            Map<String, Double> payload = new LinkedHashMap<>();
            payload.put("averageRating", service.averageRating());
            return new Result(200, payload);
        }
        throw ApiException.notFound("no route for " + method + " " + path);
    }

    /** Returns the part after /api/feedback, or null for the collection root. */
    private static String subPath(String path) {
        String prefix = "/api/feedback";
        String rest = path.startsWith(prefix) ? path.substring(prefix.length()) : path;
        rest = rest.replaceAll("^/+", "").replaceAll("/+$", "");
        return rest.isEmpty() ? null : rest;
    }

    public static class SubmitRequest {
        public String userId;
        public int rating;
        public String message;
    }
}
