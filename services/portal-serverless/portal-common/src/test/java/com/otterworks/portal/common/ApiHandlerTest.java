package com.otterworks.portal.common;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class ApiHandlerTest {

    @Test
    void decodeUtf8StrictAcceptsValidUtf8() {
        String json = "{\"userId\":\"aliç\"}";
        assertEquals(json, ApiHandler.decodeUtf8Strict(json.getBytes(StandardCharsets.UTF_8)));
    }

    @Test
    void decodeUtf8StrictRejectsInvalidUtf8() {
        byte[] invalid = {'{', '"', 'a', '"', ':', '"', (byte) 0xC3, (byte) 0x28, '"', '}'};
        ApiException e = assertThrows(ApiException.class, () -> ApiHandler.decodeUtf8Strict(invalid));
        assertEquals(400, e.getStatus());
    }

    @Test
    void decodePathDecodesSegmentsAndKeepsPlusLiteral() {
        assertEquals("/api/preferences/a b+c", ApiHandler.decodePath("/api/preferences/a%20b+c"));
    }

    @Test
    void chaosFaultThrowsOnlyForInvokeError() {
        ApiHandler.failIfChaosConfigured(null);
        ApiHandler.failIfChaosConfigured("");
        ApiHandler.failIfChaosConfigured("off");
        assertThrows(IllegalStateException.class,
                () -> ApiHandler.failIfChaosConfigured("invoke-error"));
    }
}
