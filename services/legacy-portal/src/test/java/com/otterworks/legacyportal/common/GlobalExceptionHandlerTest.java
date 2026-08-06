package com.otterworks.legacyportal.common;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import java.util.NoSuchElementException;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

/**
 * Direct unit cases for the previously untested {@link GlobalExceptionHandler} (WP-12),
 * including the null-message edge that the controller tests cannot reach.
 */
class GlobalExceptionHandlerTest {

    private final GlobalExceptionHandler handler = new GlobalExceptionHandler();

    @Test
    void aMissingElementBecomesA404WithTheExceptionMessage() {
        ResponseEntity<Map<String, String>> response =
                handler.handleNotFound(new NoSuchElementException("announcement 7 not found"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(response.getBody())
                .containsEntry("error", "Not Found")
                .containsEntry("message", "announcement 7 not found");
    }

    @Test
    void anIllegalArgumentBecomesA400WithTheExceptionMessage() {
        ResponseEntity<Map<String, String>> response =
                handler.handleBadRequest(new IllegalArgumentException("rating must be between 1 and 5"));

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(response.getBody())
                .containsEntry("error", "Bad Request")
                .containsEntry("message", "rating must be between 1 and 5");
    }

    @Test
    void aNullExceptionMessageStillProducesAWellFormedBody() {
        // Boundary: the body is built with LinkedHashMap#put, which tolerates a null
        // value -- so an exception thrown with no message must not 500.
        ResponseEntity<Map<String, String>> response = handler.handleNotFound(new NoSuchElementException());

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(response.getBody()).containsEntry("error", "Not Found").containsEntry("message", null);
    }

    @Test
    void anEmptyMessageIsPreservedRatherThanReplaced() {
        ResponseEntity<Map<String, String>> response = handler.handleBadRequest(new IllegalArgumentException(""));

        assertThat(response.getBody()).containsEntry("message", "");
    }

    @Test
    void theBodyCarriesExactlyTheErrorAndMessageKeys() {
        ResponseEntity<Map<String, String>> response =
                handler.handleNotFound(new NoSuchElementException("gone"));

        assertThat(response.getBody()).containsOnlyKeys("error", "message");
    }
}
