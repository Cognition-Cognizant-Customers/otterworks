import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/**
 * Local fixture harness for the portal Lambdas: serves the golden transcript's
 * HTTP surface by translating each request into an API Gateway HTTP API (payload
 * v2) event and invoking the real handler classes against LocalStack DynamoDB.
 * No live AWS resources are involved.
 *
 * Env: FIXTURE_PORT (default 9095), DYNAMO_ENDPOINT (default http://localhost:4566),
 *      TABLE_PREFIX (default ow-tp-portal-fixture).
 */
public final class PortalFixtureShim {

    public static void main(String[] args) throws IOException {
        int port = Integer.parseInt(env("FIXTURE_PORT", "9095"));
        String endpoint = env("DYNAMO_ENDPOINT", "http://localhost:4566");
        String prefix = env("TABLE_PREFIX", "ow-tp-portal-fixture");

        DynamoDbClient client = DynamoDbClient.builder()
                .endpointOverride(URI.create(endpoint))
                .region(Region.US_EAST_1)
                .credentialsProvider(StaticCredentialsProvider.create(
                        AwsBasicCredentials.create("test", "test")))
                .build();

        var announcements = new com.otterworks.portal.announcements.Handler(
                new com.otterworks.portal.announcements.AnnouncementService(
                        new com.otterworks.portal.announcements.DynamoAnnouncementStore(
                                client, prefix + "-announcements")));
        var preferences = new com.otterworks.portal.preferences.Handler(
                new com.otterworks.portal.preferences.PreferenceService(
                        new com.otterworks.portal.preferences.DynamoPreferenceStore(
                                client, prefix + "-preferences")));
        var feedback = new com.otterworks.portal.feedback.Handler(
                new com.otterworks.portal.feedback.FeedbackService(
                        new com.otterworks.portal.feedback.DynamoFeedbackStore(
                                client, prefix + "-feedback")));

        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getRawPath();
            var handler = path.startsWith("/api/preferences") ? preferences
                    : path.startsWith("/api/feedback") ? feedback
                    : announcements; // /api/announcements and /health (shared health route)
            try {
                APIGatewayV2HTTPResponse response = handler.handleRequest(toEvent(exchange), null);
                reply(exchange, response.getStatusCode(),
                        response.getBody() == null ? "" : response.getBody());
            } catch (RuntimeException e) {
                // Parity with a real failed invocation surfaced through API Gateway.
                reply(exchange, 500, "{\"message\":\"Internal Server Error\"}");
            }
        });
        server.start();
        System.out.println("portal fixture shim listening on :" + port
                + " (dynamo " + endpoint + ", prefix " + prefix + ")");
    }

    private static APIGatewayV2HTTPEvent toEvent(HttpExchange exchange) throws IOException {
        String body;
        try (InputStream in = exchange.getRequestBody()) {
            body = new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
        Map<String, String> query = new HashMap<>();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        if (rawQuery != null) {
            for (String pair : rawQuery.split("&")) {
                int eq = pair.indexOf('=');
                if (eq > 0) {
                    query.put(URLDecoder.decode(pair.substring(0, eq), StandardCharsets.UTF_8),
                            URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8));
                }
            }
        }
        return APIGatewayV2HTTPEvent.builder()
                .withRawPath(exchange.getRequestURI().getRawPath())
                .withQueryStringParameters(query.isEmpty() ? null : query)
                .withBody(body.isEmpty() ? null : body)
                .withIsBase64Encoded(false)
                .withRequestContext(APIGatewayV2HTTPEvent.RequestContext.builder()
                        .withHttp(APIGatewayV2HTTPEvent.RequestContext.Http.builder()
                                .withMethod(exchange.getRequestMethod())
                                .withPath(exchange.getRequestURI().getRawPath())
                                .build())
                        .build())
                .build();
    }

    private static void reply(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }
}
