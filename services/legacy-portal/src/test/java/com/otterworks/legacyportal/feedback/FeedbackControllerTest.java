package com.otterworks.legacyportal.feedback;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

/**
 * Endpoint-level cases for {@link FeedbackController}: the 1..5 rating trio at both ends, the
 * declared length limits, malformed payloads and cross-user visibility.
 *
 * <p>{@code @Transactional} keeps every row out of the shared in-memory H2 instance, so the
 * average-rating assertions see only the rows the test itself submitted and no other suite can be
 * perturbed by this one.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class FeedbackControllerTest {

    private static final int MIN_RATING = 1;
    private static final int MAX_RATING = 5;
    private static final int MAX_USER_ID = 100;
    private static final int MAX_MESSAGE = 2000;

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;

    // ------------------------------------------------------------------ submit

    @Test
    void submitReturns201WithTheStoredFeedback() throws Exception {
        mockMvc.perform(postFeedback("user-happy", 4, "Works well"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").isNumber())
                .andExpect(jsonPath("$.userId").value("user-happy"))
                .andExpect(jsonPath("$.rating").value(4))
                .andExpect(jsonPath("$.message").value("Works well"))
                .andExpect(jsonPath("$.createdAt").isNotEmpty());
    }

    // ---------------------------------------------------------- rating trios

    @Test
    void ratingOneBelowTheMinimumIsRejected() throws Exception {
        mockMvc.perform(postFeedback("user-rating", MIN_RATING - 1, "too low"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void ratingExactlyAtTheMinimumIsAccepted() throws Exception {
        mockMvc.perform(postFeedback("user-rating", MIN_RATING, "lowest allowed"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.rating").value(MIN_RATING));
    }

    @Test
    void ratingOneAboveTheMinimumIsAccepted() throws Exception {
        mockMvc.perform(postFeedback("user-rating", MIN_RATING + 1, "just above the floor"))
                .andExpect(status().isCreated());
    }

    @Test
    void ratingOneBelowTheMaximumIsAccepted() throws Exception {
        mockMvc.perform(postFeedback("user-rating", MAX_RATING - 1, "just below the ceiling"))
                .andExpect(status().isCreated());
    }

    @Test
    void ratingExactlyAtTheMaximumIsAccepted() throws Exception {
        mockMvc.perform(postFeedback("user-rating", MAX_RATING, "highest allowed"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.rating").value(MAX_RATING));
    }

    @Test
    void ratingOneAboveTheMaximumIsRejected() throws Exception {
        mockMvc.perform(postFeedback("user-rating", MAX_RATING + 1, "too high"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aNegativeRatingIsRejected() throws Exception {
        mockMvc.perform(postFeedback("user-rating", -1, "negative")).andExpect(status().isBadRequest());
    }

    @Test
    void anExtremeRatingIsRejected() throws Exception {
        mockMvc.perform(postFeedback("user-rating", Integer.MAX_VALUE, "overflowing"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void anOmittedRatingDefaultsToZeroAndIsRejected() throws Exception {
        // The DTO uses a primitive int, so an absent rating silently becomes 0 and is then
        // caught by @Min(1) rather than by a "field is required" message.
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"userId\":\"user-rating\",\"message\":\"no rating\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aNonNumericRatingIsRejected() throws Exception {
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(
                                        "{\"userId\":\"user-rating\",\"rating\":\"five\",\"message\":\"words\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aFractionalRatingIsSilentlyTruncated() throws Exception {
        // FINDING (documented, not fixed here): Jackson coerces a JSON float into the DTO's
        // primitive int by truncation, so a 4.5-star submission is stored as 4 with no error
        // and no warning to the caller.
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(
                                        "{\"userId\":\"user-rating\",\"rating\":4.5,\"message\":\"half star\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.rating").value(4));
    }

    @Test
    @Disabled("FINDING: a fractional rating is truncated to an int instead of being rejected as malformed")
    void aFractionalRatingShouldBeRejected() throws Exception {
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(
                                        "{\"userId\":\"user-rating\",\"rating\":4.5,\"message\":\"half star\"}"))
                .andExpect(status().isBadRequest());
    }

    // ---------------------------------------------------------- length trios

    @Test
    void aUserIdOneCharacterUnderTheLimitIsAccepted() throws Exception {
        mockMvc.perform(postFeedback(repeat('u', MAX_USER_ID - 1), 3, "ok"))
                .andExpect(status().isCreated());
    }

    @Test
    void aUserIdExactlyAtTheLimitIsAccepted() throws Exception {
        mockMvc.perform(postFeedback(repeat('u', MAX_USER_ID), 3, "ok")).andExpect(status().isCreated());
    }

    @Test
    void aUserIdOneCharacterOverTheLimitIsRejected() throws Exception {
        mockMvc.perform(postFeedback(repeat('u', MAX_USER_ID + 1), 3, "ok"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aMessageOneCharacterUnderTheLimitIsAccepted() throws Exception {
        mockMvc.perform(postFeedback("user-length", 3, repeat('m', MAX_MESSAGE - 1)))
                .andExpect(status().isCreated());
    }

    @Test
    void aMessageExactlyAtTheLimitIsAccepted() throws Exception {
        mockMvc.perform(postFeedback("user-length", 3, repeat('m', MAX_MESSAGE)))
                .andExpect(status().isCreated());
    }

    @Test
    void aMessageOneCharacterOverTheLimitIsRejected() throws Exception {
        mockMvc.perform(postFeedback("user-length", 3, repeat('m', MAX_MESSAGE + 1)))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aSingleCharacterMessageIsAccepted() throws Exception {
        mockMvc.perform(postFeedback("user-length", 3, "x")).andExpect(status().isCreated());
    }

    // -------------------------------------------------------- malformed submit

    @Test
    void submitRejectsAnEmptyBody() throws Exception {
        mockMvc.perform(post("/api/feedback").contentType(MediaType.APPLICATION_JSON).content(""))
                .andExpect(status().isBadRequest());
    }

    @Test
    void submitRejectsBrokenJson() throws Exception {
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"userId\":\"u\",\"rating\":"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void submitRejectsAMissingContentType() throws Exception {
        mockMvc.perform(post("/api/feedback").content("{\"userId\":\"u\",\"rating\":3,\"message\":\"m\"}"))
                .andExpect(status().isUnsupportedMediaType());
    }

    @Test
    void submitRejectsABlankUserId() throws Exception {
        mockMvc.perform(postFeedback("   ", 3, "blank user")).andExpect(status().isBadRequest());
    }

    @Test
    void submitRejectsAMissingUserId() throws Exception {
        mockMvc.perform(
                        post("/api/feedback")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"rating\":3,\"message\":\"no user\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void submitRejectsABlankMessage() throws Exception {
        mockMvc.perform(postFeedback("user-blank-message", 3, "  ")).andExpect(status().isBadRequest());
    }

    @Test
    void submitReportsEveryViolationAtOnce() throws Exception {
        mockMvc.perform(postFeedback("", 99, "")).andExpect(status().isBadRequest());
    }

    // -------------------------------------------------------------- list by user

    @Test
    void listForUserReturnsOnlyThatUsersFeedback() throws Exception {
        mockMvc.perform(postFeedback("user-alpha", 5, "alpha one")).andExpect(status().isCreated());
        mockMvc.perform(postFeedback("user-alpha", 4, "alpha two")).andExpect(status().isCreated());
        mockMvc.perform(postFeedback("user-beta", 2, "beta one")).andExpect(status().isCreated());

        mockMvc.perform(get("/api/feedback").param("userId", "user-alpha"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].userId").value("user-alpha"))
                .andExpect(jsonPath("$[1].userId").value("user-alpha"));
    }

    @Test
    void listForUserIsNewestFirst() throws Exception {
        mockMvc.perform(postFeedback("user-ordered", 1, "older")).andExpect(status().isCreated());
        mockMvc.perform(postFeedback("user-ordered", 5, "newer")).andExpect(status().isCreated());

        mockMvc.perform(get("/api/feedback").param("userId", "user-ordered"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].message").value("newer"))
                .andExpect(jsonPath("$[1].message").value("older"));
    }

    @Test
    void listForAnUnknownUserIsAnEmptyArray() throws Exception {
        mockMvc.perform(get("/api/feedback").param("userId", "nobody-at-all"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void listWithoutTheUserIdParameterIsRejected() throws Exception {
        mockMvc.perform(get("/api/feedback")).andExpect(status().isBadRequest());
    }

    @Test
    void listWithAnEmptyUserIdMatchesNobody() throws Exception {
        mockMvc.perform(get("/api/feedback").param("userId", ""))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void userIdsAreMatchedCaseSensitively() throws Exception {
        mockMvc.perform(postFeedback("user-Case", 3, "mixed case")).andExpect(status().isCreated());

        mockMvc.perform(get("/api/feedback").param("userId", "user-case"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    // ------------------------------------------------------------------- authz

    @Test
    void anyCallerCanCurrentlyReadAnotherUsersFeedback() throws Exception {
        // FINDING (documented, not fixed here): /api/feedback takes the owner from a query
        // parameter and legacy-portal has no authentication layer, so any caller can read
        // any user's feedback simply by naming them. There is no caller identity to compare
        // against Feedback.userId.
        mockMvc.perform(postFeedback("user-private", 1, "for my eyes only"))
                .andExpect(status().isCreated());

        mockMvc.perform(get("/api/feedback").param("userId", "user-private"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].message").value("for my eyes only"));
    }

    @Test
    @Disabled("FINDING: legacy-portal has no authentication — reading another user's feedback should be rejected")
    void readingAnotherUsersFeedbackShouldBeRejected() throws Exception {
        mockMvc.perform(postFeedback("user-private-2", 1, "secret")).andExpect(status().isCreated());

        mockMvc.perform(get("/api/feedback").param("userId", "user-private-2").header("X-User-ID", "someone-else"))
                .andExpect(status().isForbidden());
    }

    @Test
    void anyCallerCanCurrentlySubmitFeedbackAsAnotherUser() throws Exception {
        // Same finding on the write path: the submitted userId is taken at face value, so
        // feedback can be attributed to a user who never wrote it.
        mockMvc.perform(postFeedback("user-impersonated", 1, "not actually from this user"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.userId").value("user-impersonated"));
    }

    // ------------------------------------------------------------ average rating

    @Test
    void averageOfASingleRatingIsThatRating() throws Exception {
        mockMvc.perform(postFeedback("user-avg", 4, "only one")).andExpect(status().isCreated());

        assertAverageIsCloseTo(4.0);
    }

    @Test
    void averageIsTheArithmeticMeanAcrossUsers() throws Exception {
        mockMvc.perform(postFeedback("user-avg-a", 1, "a")).andExpect(status().isCreated());
        mockMvc.perform(postFeedback("user-avg-b", 5, "b")).andExpect(status().isCreated());

        assertAverageIsCloseTo(3.0);
    }

    @Test
    void averageIsNotRoundedToAWholeStar() throws Exception {
        mockMvc.perform(postFeedback("user-avg-c", 1, "a")).andExpect(status().isCreated());
        mockMvc.perform(postFeedback("user-avg-c", 2, "b")).andExpect(status().isCreated());

        assertAverageIsCloseTo(1.5);
    }

    @Test
    void aRepeatingAverageKeepsFullDoublePrecision() throws Exception {
        mockMvc.perform(postFeedback("user-avg-d", 1, "a")).andExpect(status().isCreated());
        mockMvc.perform(postFeedback("user-avg-d", 1, "b")).andExpect(status().isCreated());
        mockMvc.perform(postFeedback("user-avg-d", 2, "c")).andExpect(status().isCreated());

        assertAverageIsCloseTo(4.0 / 3.0);
    }

    @Test
    void averageStaysWithinTheAllowedRatingRange() throws Exception {
        for (int rating = MIN_RATING; rating <= MAX_RATING; rating++) {
            mockMvc.perform(postFeedback("user-avg-range", rating, "r" + rating))
                    .andExpect(status().isCreated());
        }

        double average = readAverage();
        org.junit.jupiter.api.Assertions.assertTrue(
                average >= MIN_RATING && average <= MAX_RATING, "average out of range: " + average);
    }

    @Test
    void theAverageEndpointRejectsTheWrongVerb() throws Exception {
        mockMvc.perform(post("/api/feedback/average-rating")).andExpect(status().isMethodNotAllowed());
    }

    // ----------------------------------------------------------------- helpers

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder postFeedback(
            String userId, int rating, String message) {
        String json =
                objectMapper
                        .createObjectNode()
                        .put("userId", userId)
                        .put("rating", rating)
                        .put("message", message)
                        .toString();
        return post("/api/feedback").contentType(MediaType.APPLICATION_JSON).content(json);
    }

    private double readAverage() throws Exception {
        String response =
                mockMvc.perform(get("/api/feedback/average-rating"))
                        .andExpect(status().isOk())
                        .andReturn()
                        .getResponse()
                        .getContentAsString();
        JsonNode node = objectMapper.readTree(response);
        return node.get("averageRating").asDouble();
    }

    private void assertAverageIsCloseTo(double expected) throws Exception {
        double actual = readAverage();
        org.junit.jupiter.api.Assertions.assertEquals(expected, actual, 1e-9);
    }

    private static String repeat(char c, int times) {
        StringBuilder sb = new StringBuilder(times);
        for (int i = 0; i < times; i++) {
            sb.append(c);
        }
        return sb.toString();
    }
}
