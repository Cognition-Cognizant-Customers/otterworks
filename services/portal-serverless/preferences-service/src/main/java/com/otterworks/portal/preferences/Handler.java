package com.otterworks.portal.preferences;

import com.otterworks.portal.common.ApiException;
import com.otterworks.portal.common.ApiHandler;
import java.util.Map;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/**
 * Lambda entry point for the user-preferences bounded context.
 *
 * <p>Routes (parity with the monolith's UserPreferenceController):
 * <ul>
 *   <li>GET /api/preferences/{userId}</li>
 *   <li>PUT /api/preferences/{userId}</li>
 * </ul>
 */
public class Handler extends ApiHandler {

    private final PreferenceService service;

    public Handler() {
        this(new PreferenceService(new DynamoPreferenceStore(
                DynamoDbClient.create(), System.getenv("TABLE_NAME"))));
    }

    public Handler(PreferenceService service) {
        this.service = service;
    }

    @Override
    protected Result route(String method, String path, Map<String, String> query, String body) {
        String userId = subPath(path);
        if (userId != null && !userId.contains("/")) {
            if ("GET".equals(method)) {
                return new Result(200, service.getOrDefault(userId));
            }
            if ("PUT".equals(method)) {
                UpdateRequest request = parseBody(body, UpdateRequest.class);
                requireText(request.theme, "theme", 20);
                requireText(request.locale, "locale", 20);
                return new Result(200,
                        service.save(userId, request.theme, request.locale, request.emailNotifications));
            }
        }
        throw ApiException.notFound("no route for " + method + " " + path);
    }

    /** Returns the part after /api/preferences, or null for the collection root. */
    private static String subPath(String path) {
        String prefix = "/api/preferences";
        String rest = path.startsWith(prefix) ? path.substring(prefix.length()) : path;
        rest = rest.replaceAll("^/+", "").replaceAll("/+$", "");
        return rest.isEmpty() ? null : rest;
    }

    public static class UpdateRequest {
        public String theme;
        public String locale;
        public boolean emailNotifications;
    }
}
