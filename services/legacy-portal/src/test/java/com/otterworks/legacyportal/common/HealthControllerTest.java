package com.otterworks.legacyportal.common;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Web-layer cases for {@link HealthController} (WP-12), including the method and
 * path negatives that the existing full-context test does not cover.
 */
@WebMvcTest(HealthController.class)
class HealthControllerTest {

    @Autowired private MockMvc mockMvc;

    @Test
    void healthReportsUpAsJson() throws Exception {
        mockMvc.perform(get("/health"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith("application/json"))
                .andExpect(jsonPath("$.status").value("UP"))
                .andExpect(jsonPath("$.service").value("legacy-portal"));
    }

    @Test
    void healthIsAReadOnlyEndpoint() {
        // The controller holds no state, so two calls are byte-identical.
        Map<String, String> first = new HealthController().health();
        Map<String, String> second = new HealthController().health();

        assertThat(first).isEqualTo(second).containsOnlyKeys("status", "service");
    }

    @Test
    void healthRejectsANonGetMethod() throws Exception {
        mockMvc.perform(post("/health")).andExpect(status().isMethodNotAllowed());
    }

    @Test
    void anUnknownPathIsNotFound() throws Exception {
        mockMvc.perform(get("/healthz")).andExpect(status().isNotFound());
    }
}
