package com.otterworks.feedback.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

/** Full-context test: the service boots and the three feedback routes are wired. */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class FeedbackControllerIntegrationTest {

  @Autowired private MockMvc mockMvc;

  @Test
  void healthEndpointReportsUp() throws Exception {
    mockMvc
        .perform(get("/health"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.status").value("UP"))
        .andExpect(jsonPath("$.service").value("feedback-service"));
  }

  @Test
  void submitListAndAverageRoundTrip() throws Exception {
    mockMvc
        .perform(
            post("/api/feedback")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"userId\":\"u1\",\"rating\":5,\"message\":\"great\"}"))
        .andExpect(status().isCreated())
        .andExpect(jsonPath("$.id").isNumber())
        .andExpect(jsonPath("$.userId").value("u1"))
        .andExpect(jsonPath("$.rating").value(5))
        .andExpect(jsonPath("$.message").value("great"))
        .andExpect(jsonPath("$.createdAt").isNotEmpty());

    mockMvc
        .perform(get("/api/feedback").param("userId", "u1"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$[0].userId").value("u1"));

    mockMvc
        .perform(get("/api/feedback/average-rating"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.averageRating").isNumber());
  }

  @Test
  void validatesRating() throws Exception {
    mockMvc
        .perform(
            post("/api/feedback")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"userId\":\"u1\",\"rating\":9,\"message\":\"bad rating\"}"))
        .andExpect(status().isBadRequest());
  }
}
