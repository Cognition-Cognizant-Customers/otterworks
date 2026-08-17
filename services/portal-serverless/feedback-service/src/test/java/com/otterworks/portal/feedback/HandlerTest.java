package com.otterworks.portal.feedback;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import java.util.stream.Collectors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class HandlerTest {

    static class InMemoryStore implements FeedbackStore {
        final List<Feedback> items = new ArrayList<>();
        final AtomicLong seq = new AtomicLong();

        public long nextId() {
            return seq.incrementAndGet();
        }

        public void put(Feedback f) {
            items.add(f);
        }

        public List<Feedback> findByUserId(String userId) {
            return items.stream()
                    .filter(f -> f.getUserId().equals(userId))
                    .collect(Collectors.toList());
        }

        public List<Feedback> findAll() {
            return new ArrayList<>(items);
        }
    }

    private Handler handler;

    @BeforeEach
    void setUp() {
        handler = new Handler(new FeedbackService(new InMemoryStore()));
    }

    private APIGatewayV2HTTPResponse call(String method, String path, String body) {
        String[] parts = path.split("\\?", 2);
        Map<String, String> query = new HashMap<>();
        if (parts.length == 2) {
            for (String pair : parts[1].split("&")) {
                String[] kv = pair.split("=", 2);
                query.put(kv[0], kv.length == 2 ? kv[1] : "");
            }
        }
        APIGatewayV2HTTPEvent event = APIGatewayV2HTTPEvent.builder()
                .withRawPath(parts[0])
                .withQueryStringParameters(query.isEmpty() ? null : query)
                .withBody(body)
                .withRequestContext(APIGatewayV2HTTPEvent.RequestContext.builder()
                        .withHttp(APIGatewayV2HTTPEvent.RequestContext.Http.builder()
                                .withMethod(method)
                                .withPath(parts[0])
                                .build())
                        .build())
                .build();
        return handler.handleRequest(event, null);
    }

    @Test
    void submitReturns201WithSequentialIds() {
        APIGatewayV2HTTPResponse first = call("POST", "/api/feedback",
                "{\"userId\":\"alice\",\"rating\":5,\"message\":\"great\"}");
        assertEquals(201, first.getStatusCode());
        assertTrue(first.getBody().contains("\"id\":1"));
        APIGatewayV2HTTPResponse second = call("POST", "/api/feedback",
                "{\"userId\":\"bob\",\"rating\":3,\"message\":\"ok\"}");
        assertTrue(second.getBody().contains("\"id\":2"));
    }

    @Test
    void ratingOutOfRangeIs400() {
        APIGatewayV2HTTPResponse response = call("POST", "/api/feedback",
                "{\"userId\":\"alice\",\"rating\":6,\"message\":\"too much\"}");
        assertEquals(400, response.getStatusCode());
        response = call("POST", "/api/feedback",
                "{\"userId\":\"alice\",\"rating\":0,\"message\":\"too little\"}");
        assertEquals(400, response.getStatusCode());
    }

    @Test
    void listForUserIsNewestFirstAndFiltered() throws InterruptedException {
        call("POST", "/api/feedback", "{\"userId\":\"alice\",\"rating\":5,\"message\":\"first\"}");
        Thread.sleep(5);
        call("POST", "/api/feedback", "{\"userId\":\"bob\",\"rating\":3,\"message\":\"other\"}");
        Thread.sleep(5);
        call("POST", "/api/feedback", "{\"userId\":\"alice\",\"rating\":4,\"message\":\"second\"}");
        String body = call("GET", "/api/feedback?userId=alice", null).getBody();
        assertTrue(body.indexOf("\"second\"") < body.indexOf("\"first\""), body);
        assertTrue(!body.contains("\"other\""), body);
    }

    @Test
    void averageRatingIsZeroWhenEmptyThenMeanOfAll() {
        assertEquals("{\"averageRating\":0.0}",
                call("GET", "/api/feedback/average-rating", null).getBody());
        call("POST", "/api/feedback", "{\"userId\":\"alice\",\"rating\":5,\"message\":\"a\"}");
        call("POST", "/api/feedback", "{\"userId\":\"bob\",\"rating\":3,\"message\":\"b\"}");
        call("POST", "/api/feedback", "{\"userId\":\"alice\",\"rating\":4,\"message\":\"c\"}");
        assertEquals("{\"averageRating\":4.0}",
                call("GET", "/api/feedback/average-rating", null).getBody());
    }

    @Test
    void missingUserIdQueryIs400() {
        assertEquals(400, call("GET", "/api/feedback", null).getStatusCode());
    }
}
