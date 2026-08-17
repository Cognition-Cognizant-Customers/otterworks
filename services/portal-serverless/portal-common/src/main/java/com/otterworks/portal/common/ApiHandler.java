package com.otterworks.portal.common;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestHandler;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import com.fasterxml.jackson.core.JsonProcessingException;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Base class for the portal Lambdas: decodes the API Gateway HTTP API (payload v2)
 * event, dispatches to {@link #route}, and maps {@link ApiException} to the
 * monolith-compatible error body.
 */
public abstract class ApiHandler implements RequestHandler<APIGatewayV2HTTPEvent, APIGatewayV2HTTPResponse> {

    /** Handle one request; throw {@link ApiException} for error responses. */
    protected abstract Result route(String method, String path, Map<String, String> query, String body);

    @Override
    public APIGatewayV2HTTPResponse handleRequest(APIGatewayV2HTTPEvent event, Context context) {
        try {
            String method = event.getRequestContext().getHttp().getMethod();
            String path = event.getRawPath();
            Map<String, String> query = event.getQueryStringParameters() == null
                    ? Map.of() : event.getQueryStringParameters();
            String body = event.getBody();
            if (body != null && Boolean.TRUE.equals(event.getIsBase64Encoded())) {
                body = new String(Base64.getDecoder().decode(body));
            }
            Result result = "GET".equals(method) && "/health".equals(path)
                    ? health()
                    : route(method, path, query, body);
            return respond(result.status, result.payload);
        } catch (ApiException e) {
            Map<String, String> error = new LinkedHashMap<>();
            error.put("error", e.getReason());
            error.put("message", e.getMessage());
            return respond(e.getStatus(), error);
        }
    }

    /**
     * Shared /health contract carried over from the monolith so existing probes keep
     * working against the decomposed estate.
     */
    private static Result health() {
        Map<String, String> body = new LinkedHashMap<>();
        body.put("status", "UP");
        body.put("service", "legacy-portal");
        return new Result(200, body);
    }

    private APIGatewayV2HTTPResponse respond(int status, Object payload) {
        String json;
        try {
            json = Json.MAPPER.writeValueAsString(payload);
        } catch (JsonProcessingException e) {
            json = "{\"error\":\"Internal Server Error\",\"message\":\"serialization\"}";
            status = 500;
        }
        return APIGatewayV2HTTPResponse.builder()
                .withStatusCode(status)
                .withHeaders(Map.of("Content-Type", "application/json"))
                .withBody(json)
                .build();
    }

    /** Status + serializable payload returned by a route. */
    public static final class Result {
        final int status;
        final Object payload;

        public Result(int status, Object payload) {
            this.status = status;
            this.payload = payload;
        }
    }

    protected static <T> T parseBody(String body, Class<T> type) {
        if (body == null || body.isBlank()) {
            throw ApiException.badRequest("request body is required");
        }
        try {
            return Json.MAPPER.readValue(body, type);
        } catch (JsonProcessingException e) {
            throw ApiException.badRequest("malformed request body");
        }
    }

    protected static long parseLong(String raw, String what) {
        try {
            return Long.parseLong(raw);
        } catch (NumberFormatException e) {
            throw ApiException.badRequest("invalid " + what + ": " + raw);
        }
    }

    protected static void requireText(String value, String field, int maxLength) {
        if (value == null || value.isBlank()) {
            throw ApiException.badRequest(field + " must not be blank");
        }
        if (value.length() > maxLength) {
            throw ApiException.badRequest(field + " must be at most " + maxLength + " characters");
        }
    }
}
