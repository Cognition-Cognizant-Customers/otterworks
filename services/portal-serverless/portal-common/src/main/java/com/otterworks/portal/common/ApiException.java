package com.otterworks.portal.common;

/**
 * HTTP-mapped exception producing the same error body shape as the monolith's
 * {@code GlobalExceptionHandler}: {@code {"error": <reason>, "message": <message>}}.
 */
public class ApiException extends RuntimeException {

    private final int status;
    private final String reason;

    public ApiException(int status, String reason, String message) {
        super(message);
        this.status = status;
        this.reason = reason;
    }

    public static ApiException notFound(String message) {
        return new ApiException(404, "Not Found", message);
    }

    public static ApiException badRequest(String message) {
        return new ApiException(400, "Bad Request", message);
    }

    public int getStatus() {
        return status;
    }

    public String getReason() {
        return reason;
    }
}
