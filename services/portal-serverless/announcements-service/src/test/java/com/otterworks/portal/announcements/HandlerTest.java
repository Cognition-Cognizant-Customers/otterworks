package com.otterworks.portal.announcements;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class HandlerTest {

    static class InMemoryStore implements AnnouncementStore {
        final Map<Long, Announcement> items = new HashMap<>();
        final AtomicLong seq = new AtomicLong();

        public long nextId() {
            return seq.incrementAndGet();
        }

        public void put(Announcement a) {
            items.put(a.getId(), copy(a));
        }

        public Optional<Announcement> find(long id) {
            return Optional.ofNullable(items.get(id)).map(HandlerTest::copy);
        }

        public List<Announcement> findAll() {
            List<Announcement> all = new ArrayList<>();
            items.values().forEach(a -> all.add(copy(a)));
            return all;
        }
    }

    static Announcement copy(Announcement a) {
        return new Announcement(a.getId(), a.getTitle(), a.getBody(), a.isPublished(), a.getCreatedAt());
    }

    private Handler handler;

    @BeforeEach
    void setUp() {
        handler = new Handler(new AnnouncementService(new InMemoryStore()));
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
    void createReturns201WithEntityAndSequentialIds() {
        APIGatewayV2HTTPResponse first =
                call("POST", "/api/announcements", "{\"title\":\"A\",\"body\":\"B\",\"published\":true}");
        assertEquals(201, first.getStatusCode());
        assertTrue(first.getBody().contains("\"id\":1"));
        assertTrue(first.getBody().contains("\"published\":true"));
        APIGatewayV2HTTPResponse second =
                call("POST", "/api/announcements", "{\"title\":\"C\",\"body\":\"D\"}");
        assertTrue(second.getBody().contains("\"id\":2"));
        assertTrue(second.getBody().contains("\"published\":false"));
    }

    @Test
    void listDefaultsToPublishedOnlyNewestFirst() throws InterruptedException {
        call("POST", "/api/announcements", "{\"title\":\"old\",\"body\":\"x\",\"published\":true}");
        Thread.sleep(5);
        call("POST", "/api/announcements", "{\"title\":\"draft\",\"body\":\"x\",\"published\":false}");
        Thread.sleep(5);
        call("POST", "/api/announcements", "{\"title\":\"new\",\"body\":\"x\",\"published\":true}");
        String body = call("GET", "/api/announcements", null).getBody();
        assertTrue(body.indexOf("\"new\"") < body.indexOf("\"old\""), body);
        assertTrue(!body.contains("\"draft\""), body);
    }

    @Test
    void listAllIsInsertionOrderIncludingDrafts() {
        call("POST", "/api/announcements", "{\"title\":\"first\",\"body\":\"x\",\"published\":true}");
        call("POST", "/api/announcements", "{\"title\":\"second\",\"body\":\"x\",\"published\":false}");
        String body = call("GET", "/api/announcements?publishedOnly=false", null).getBody();
        assertTrue(body.indexOf("\"first\"") < body.indexOf("\"second\""), body);
    }

    @Test
    void publishFlipsDraftToPublished() {
        call("POST", "/api/announcements", "{\"title\":\"d\",\"body\":\"x\",\"published\":false}");
        APIGatewayV2HTTPResponse response = call("POST", "/api/announcements/1/publish", null);
        assertEquals(200, response.getStatusCode());
        assertTrue(response.getBody().contains("\"published\":true"));
    }

    @Test
    void missingAnnouncementIs404WithMonolithErrorBody() {
        APIGatewayV2HTTPResponse response = call("GET", "/api/announcements/999", null);
        assertEquals(404, response.getStatusCode());
        assertEquals(
                "{\"error\":\"Not Found\",\"message\":\"announcement 999 not found\"}",
                response.getBody());
    }

    @Test
    void blankTitleIs400() {
        APIGatewayV2HTTPResponse response =
                call("POST", "/api/announcements", "{\"title\":\"\",\"body\":\"x\"}");
        assertEquals(400, response.getStatusCode());
    }

    @Test
    void nonNumericIdIs400() {
        assertEquals(400, call("GET", "/api/announcements/abc", null).getStatusCode());
    }
}
