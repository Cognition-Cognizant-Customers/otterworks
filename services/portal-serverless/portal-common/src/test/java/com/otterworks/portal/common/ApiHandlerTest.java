package com.otterworks.portal.common;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayV2HTTPResponse;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ApiHandlerTest {

    private static final class TestHandler extends ApiHandler {
        private final RuntimeException failure;

        private TestHandler(RuntimeException failure) {
            this.failure = failure;
        }

        @Override
        protected Result route(String method, String path, Map<String, String> query, String body) {
            if (failure != null) {
                throw failure;
            }
            throw ApiException.badRequest("bad input");
        }
    }

    @Test
    void apiExceptionMappingRemainsUnchanged() {
        APIGatewayV2HTTPResponse response = new TestHandler(null).handleRequest(event(), null);

        assertEquals(400, response.getStatusCode());
        assertEquals("{\"error\":\"Bad Request\",\"message\":\"bad input\"}", response.getBody());
    }

    @Test
    void unexpectedRuntimeExceptionPropagates() {
        RuntimeException failure = new RuntimeException("store unavailable");

        RuntimeException thrown = assertThrows(
                RuntimeException.class,
                () -> new TestHandler(failure).handleRequest(event(), null));

        assertEquals(failure, thrown);
    }

    private static APIGatewayV2HTTPEvent event() {
        return APIGatewayV2HTTPEvent.builder()
                .withRawPath("/api/test")
                .withRequestContext(APIGatewayV2HTTPEvent.RequestContext.builder()
                        .withHttp(APIGatewayV2HTTPEvent.RequestContext.Http.builder()
                                .withMethod("GET")
                                .withPath("/api/test")
                                .build())
                        .build())
                .build();
    }
}
