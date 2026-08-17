package com.otterworks.portal.announcements;

import com.otterworks.portal.common.ApiException;
import com.otterworks.portal.common.ApiHandler;
import java.util.Map;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/**
 * Lambda entry point for the announcements bounded context.
 *
 * <p>Routes (parity with the monolith's AnnouncementController):
 * <ul>
 *   <li>GET  /api/announcements?publishedOnly=true|false</li>
 *   <li>POST /api/announcements</li>
 *   <li>GET  /api/announcements/{id}</li>
 *   <li>POST /api/announcements/{id}/publish</li>
 * </ul>
 */
public class Handler extends ApiHandler {

    private final AnnouncementService service;

    public Handler() {
        this(new AnnouncementService(new DynamoAnnouncementStore(
                DynamoDbClient.create(), System.getenv("TABLE_NAME"))));
    }

    public Handler(AnnouncementService service) {
        this.service = service;
    }

    @Override
    protected Result route(String method, String path, Map<String, String> query, String body) {
        String rest = subPath(path);
        if (rest == null) {
            if ("GET".equals(method)) {
                boolean publishedOnly = Boolean.parseBoolean(query.getOrDefault("publishedOnly", "true"));
                return new Result(200, publishedOnly ? service.listPublished() : service.listAll());
            }
            if ("POST".equals(method)) {
                CreateRequest request = parseBody(body, CreateRequest.class);
                requireText(request.title, "title", 200);
                requireText(request.body, "body", 4000);
                return new Result(201, service.create(request.title, request.body, request.published));
            }
        } else if (rest.endsWith("/publish") && "POST".equals(method)) {
            long id = parseLong(rest.substring(0, rest.length() - "/publish".length()), "id");
            return new Result(200, service.publish(id));
        } else if (!rest.contains("/") && "GET".equals(method)) {
            return new Result(200, service.get(parseLong(rest, "id")));
        }
        throw ApiException.notFound("no route for " + method + " " + path);
    }

    /** Returns the part after /api/announcements, or null for the collection root. */
    private static String subPath(String path) {
        String prefix = "/api/announcements";
        String rest = path.startsWith(prefix) ? path.substring(prefix.length()) : path;
        rest = rest.replaceAll("^/+", "").replaceAll("/+$", "");
        return rest.isEmpty() ? null : rest;
    }

    public static class CreateRequest {
        public String title;
        public String body;
        public boolean published;
    }
}
