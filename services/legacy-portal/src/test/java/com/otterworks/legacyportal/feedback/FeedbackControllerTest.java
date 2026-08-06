package com.otterworks.legacyportal.feedback;

import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Arrays;
import java.util.Collections;
import java.util.stream.Collectors;
import java.util.stream.IntStream;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Web-layer cases for the previously untested {@link FeedbackController} (WP-12).
 *
 * The rating bounds are enforced twice -- by {@code @Min}/{@code @Max} on the payload
 * and again by {@link FeedbackService} -- so both are exercised: the trio below the
 * minimum and above the maximum here, and the service-level trio in
 * {@code FeedbackServiceBoundaryTest}.
 */
@WebMvcTest(FeedbackController.class)
class FeedbackControllerTest {

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;

    @MockBean private FeedbackService service;

    private static String repeat(char c, int length) {
        return IntStream.range(0, length).mapToObj(i -> String.valueOf(c)).collect(Collectors.joining());
    }

    private String submitBody(String userId, int rating, String message) throws Exception {
        return objectMapper.writeValueAsString(
                objectMapper.createObjectNode().put("userId", userId).put("rating", rating).put("message", message));
    }

    private void expectSubmitStatus(String userId, int rating, String message, int expectedStatus)
            throws Exception {
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(submitBody(userId, rating, message)))
                .andExpect(status().is(expectedStatus));
    }

    // ---- submit: rating boundary trios at both ends ----

    @Test
    @DisplayName("rating honours the lower bound trio 0 / 1 / 2")
    void ratingLowerBoundTrio() throws Exception {
        given(service.submit(anyString(), anyInt(), anyString()))
                .willReturn(new Feedback("user-1", 1, "ok"));

        expectSubmitStatus("user-1", 0, "too low", 400);
        expectSubmitStatus("user-1", 1, "at the minimum", 201);
        expectSubmitStatus("user-1", 2, "above the minimum", 201);
    }

    @Test
    @DisplayName("rating honours the upper bound trio 4 / 5 / 6")
    void ratingUpperBoundTrio() throws Exception {
        given(service.submit(anyString(), anyInt(), anyString()))
                .willReturn(new Feedback("user-1", 5, "ok"));

        expectSubmitStatus("user-1", 4, "below the maximum", 201);
        expectSubmitStatus("user-1", 5, "at the maximum", 201);
        expectSubmitStatus("user-1", 6, "too high", 400);
    }

    @Test
    void aNegativeOrAbsentRatingIsRejected() throws Exception {
        expectSubmitStatus("user-1", -1, "negative", 400);

        // An absent rating deserialises to the int default 0, which fails @Min(1).
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"userId\":\"user-1\",\"message\":\"no rating\"}"))
                .andExpect(status().isBadRequest());

        verify(service, never()).submit(anyString(), anyInt(), anyString());
    }

    @Test
    @DisplayName("message honours the 2000-character boundary trio")
    void messageLengthBoundaryTrio() throws Exception {
        given(service.submit(anyString(), anyInt(), anyString()))
                .willReturn(new Feedback("user-1", 3, "ok"));

        expectSubmitStatus("user-1", 3, repeat('m', 1999), 201);
        expectSubmitStatus("user-1", 3, repeat('m', 2000), 201);
        expectSubmitStatus("user-1", 3, repeat('m', 2001), 400);
    }

    @Test
    @DisplayName("userId honours the 100-character boundary trio")
    void userIdLengthBoundaryTrio() throws Exception {
        given(service.submit(anyString(), anyInt(), anyString()))
                .willReturn(new Feedback("user-1", 3, "ok"));

        expectSubmitStatus(repeat('u', 99), 3, "ok", 201);
        expectSubmitStatus(repeat('u', 100), 3, "ok", 201);
        expectSubmitStatus(repeat('u', 101), 3, "ok", 400);
    }

    @Test
    void blankUserIdOrMessageIsRejected() throws Exception {
        expectSubmitStatus("   ", 3, "ok", 400);
        expectSubmitStatus("user-1", 3, "   ", 400);
    }

    @Test
    void malformedJsonIsRejected() throws Exception {
        mockMvc.perform(post("/api/feedback").contentType(MediaType.APPLICATION_JSON).content("{\"rating\":"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aNonNumericRatingIsRejected() throws Exception {
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"userId\":\"user-1\",\"rating\":\"five\",\"message\":\"ok\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aServiceLevelRatingRejectionSurfacesAs400() throws Exception {
        // The service throws IllegalArgumentException; the global advice maps it to 400.
        given(service.submit(anyString(), anyInt(), anyString()))
                .willThrow(new IllegalArgumentException("rating must be between 1 and 5"));

        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(submitBody("user-1", 3, "ok")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message").value("rating must be between 1 and 5"));
    }

    @Test
    void aSubmittedFeedbackIsEchoedBack() throws Exception {
        given(service.submit("user-1", 4, "great")).willReturn(new Feedback("user-1", 4, "great"));

        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(submitBody("user-1", 4, "great")))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.userId").value("user-1"))
                .andExpect(jsonPath("$.rating").value(4))
                .andExpect(jsonPath("$.createdAt").exists());
    }

    // ---- list ----

    @Test
    void listRequiresTheUserIdParameter() throws Exception {
        mockMvc.perform(get("/api/feedback")).andExpect(status().isBadRequest());
        verify(service, never()).listForUser(anyString());
    }

    @Test
    void listAcceptsAnEmptyUserIdAndReturnsNothing() throws Exception {
        given(service.listForUser("")).willReturn(Collections.emptyList());

        mockMvc.perform(get("/api/feedback").param("userId", ""))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void listReturnsEveryEntryForTheUser() throws Exception {
        given(service.listForUser("user-1"))
                .willReturn(Arrays.asList(new Feedback("user-1", 5, "a"), new Feedback("user-1", 1, "b")));

        mockMvc.perform(get("/api/feedback").param("userId", "user-1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].rating").value(5));
    }

    @Test
    @DisplayName("authz negative: any caller can read another user's feedback")
    void anotherUsersFeedbackIsReadableWithoutAuthentication() throws Exception {
        // FINDING (WP-12, authz negative): `GET /api/feedback?userId=` trusts the query
        // parameter as the identity. There is no authentication and no ownership check,
        // so anyone can enumerate another user's free-text feedback. Pinned, not fixed.
        given(service.listForUser("someone-else"))
                .willReturn(Collections.singletonList(new Feedback("someone-else", 2, "private complaint")));

        mockMvc.perform(get("/api/feedback").param("userId", "someone-else"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].message").value("private complaint"));
    }

    // ---- average rating ----

    @Test
    void averageRatingOfAnEmptyCorpusIsZero() throws Exception {
        given(service.averageRating()).willReturn(0.0);

        mockMvc.perform(get("/api/feedback/average-rating"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.averageRating").value(0.0));
    }

    @Test
    void averageRatingIsReportedUnrounded() throws Exception {
        given(service.averageRating()).willReturn(3.3333333333333335);

        mockMvc.perform(get("/api/feedback/average-rating"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.averageRating").value(3.3333333333333335));
    }

    @Test
    void averageRatingIsOpenToAnonymousCallers() throws Exception {
        // Aggregate feedback across every user is readable without authentication.
        given(service.averageRating()).willReturn(4.5);

        mockMvc.perform(get("/api/feedback/average-rating")).andExpect(status().isOk());
    }
}
