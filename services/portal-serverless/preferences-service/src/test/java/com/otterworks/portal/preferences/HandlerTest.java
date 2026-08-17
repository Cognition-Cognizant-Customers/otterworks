package com.otterworks.portal.preferences;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class HandlerTest {

    static class InMemoryStore implements PreferenceStore {
        final Map<String, UserPreference> items = new HashMap<>();

        public Optional<UserPreference> find(String userId) {
            return Optional.ofNullable(items.get(userId));
        }

        public void put(UserPreference p) {
            items.put(p.getUserId(), p);
        }
    }

    private Handler handler;

    @BeforeEach
    void setUp() {
        handler = new Handler(new PreferenceService(new InMemoryStore()));
    }

    private APIGatewayV2HTTPResponse call(String method, String path, String body) {
        APIGatewayV2HTTPEvent event = APIGatewayV2HTTPEvent.builder()
                .withRawPath(path)
                .withBody(body)
                .withRequestContext(APIGatewayV2HTTPEvent.RequestContext.builder()
                        .withHttp(APIGatewayV2HTTPEvent.RequestContext.Http.builder()
                                .withMethod(method)
                                .withPath(path)
                                .build())
                        .build())
                .build();
        return handler.handleRequest(event, null);
    }

    @Test
    void unknownUserGetsDefaults() {
        APIGatewayV2HTTPResponse response = call("GET", "/api/preferences/alice", null);
        assertEquals(200, response.getStatusCode());
        assertEquals(
                "{\"userId\":\"alice\",\"theme\":\"light\",\"locale\":\"en-US\",\"emailNotifications\":true}",
                response.getBody());
    }

    @Test
    void updateThenGetRoundTrips() {
        APIGatewayV2HTTPResponse update = call("PUT", "/api/preferences/alice",
                "{\"theme\":\"dark\",\"locale\":\"fr-FR\",\"emailNotifications\":false}");
        assertEquals(200, update.getStatusCode());
        String expected =
                "{\"userId\":\"alice\",\"theme\":\"dark\",\"locale\":\"fr-FR\",\"emailNotifications\":false}";
        assertEquals(expected, update.getBody());
        assertEquals(expected, call("GET", "/api/preferences/alice", null).getBody());
    }

    @Test
    void blankThemeIs400() {
        APIGatewayV2HTTPResponse response = call("PUT", "/api/preferences/alice",
                "{\"theme\":\"\",\"locale\":\"en-US\",\"emailNotifications\":true}");
        assertEquals(400, response.getStatusCode());
    }

    @Test
    void collectionRootIs404() {
        assertEquals(404, call("GET", "/api/preferences", null).getStatusCode());
    }
}
