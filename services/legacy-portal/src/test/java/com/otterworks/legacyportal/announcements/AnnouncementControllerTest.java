package com.otterworks.legacyportal.announcements;

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
 * Endpoint-level cases for {@link AnnouncementController}: happy path, malformed input, unknown
 * identifiers and the length boundaries declared on the request DTO.
 *
 * <p>The class is {@code @Transactional} so every request is rolled back: the modular monolith
 * shares one in-memory H2 instance across the whole test JVM, and committing rows here would leak
 * into other modules' suites and make ordering matter.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class AnnouncementControllerTest {

    private static final int MAX_TITLE = 200;
    private static final int MAX_BODY = 4000;

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;

    // ------------------------------------------------------------------ create

    @Test
    void createReturns201WithTheStoredAnnouncement() throws Exception {
        mockMvc.perform(postAnnouncement("Quarterly update", "All systems nominal", true))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").isNumber())
                .andExpect(jsonPath("$.title").value("Quarterly update"))
                .andExpect(jsonPath("$.body").value("All systems nominal"))
                .andExpect(jsonPath("$.published").value(true))
                .andExpect(jsonPath("$.createdAt").isNotEmpty());
    }

    @Test
    void createDefaultsToUnpublishedWhenTheFlagIsOmitted() throws Exception {
        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"title\":\"Draft\",\"body\":\"Not yet public\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.published").value(false));
    }

    @Test
    void createIgnoresUnknownFields() throws Exception {
        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(
                                        "{\"title\":\"Extra\",\"body\":\"Fields\",\"published\":false,\"nonsense\":1}"))
                .andExpect(status().isCreated());
    }

    // -------------------------------------------------------- malformed create

    @Test
    void createRejectsAnEmptyBody() throws Exception {
        mockMvc.perform(post("/api/announcements").contentType(MediaType.APPLICATION_JSON).content(""))
                .andExpect(status().isBadRequest());
    }

    @Test
    void createRejectsBrokenJson() throws Exception {
        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"title\":\"Broken\","))
                .andExpect(status().isBadRequest());
    }

    @Test
    void createRejectsAMissingContentType() throws Exception {
        mockMvc.perform(post("/api/announcements").content("{\"title\":\"t\",\"body\":\"b\"}"))
                .andExpect(status().isUnsupportedMediaType());
    }

    @Test
    void createRejectsAMissingTitle() throws Exception {
        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"body\":\"No title\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void createRejectsABlankTitle() throws Exception {
        mockMvc.perform(postAnnouncement("   ", "Whitespace title", false))
                .andExpect(status().isBadRequest());
    }

    @Test
    void createRejectsABlankBody() throws Exception {
        mockMvc.perform(postAnnouncement("Blank body", "", false)).andExpect(status().isBadRequest());
    }

    @Test
    void createRejectsAWronglyTypedField() throws Exception {
        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"title\":\"t\",\"body\":\"b\",\"published\":\"maybe\"}"))
                .andExpect(status().isBadRequest());
    }

    // ------------------------------------------------------- length boundaries

    @Test
    void titleOneCharacterUnderTheLimitIsAccepted() throws Exception {
        mockMvc.perform(postAnnouncement(repeat('t', MAX_TITLE - 1), "body", false))
                .andExpect(status().isCreated());
    }

    @Test
    void titleExactlyAtTheLimitIsAccepted() throws Exception {
        mockMvc.perform(postAnnouncement(repeat('t', MAX_TITLE), "body", false))
                .andExpect(status().isCreated());
    }

    @Test
    void titleOneCharacterOverTheLimitIsRejected() throws Exception {
        mockMvc.perform(postAnnouncement(repeat('t', MAX_TITLE + 1), "body", false))
                .andExpect(status().isBadRequest());
    }

    @Test
    void bodyOneCharacterUnderTheLimitIsAccepted() throws Exception {
        mockMvc.perform(postAnnouncement("title", repeat('b', MAX_BODY - 1), false))
                .andExpect(status().isCreated());
    }

    @Test
    void bodyExactlyAtTheLimitIsAccepted() throws Exception {
        mockMvc.perform(postAnnouncement("title", repeat('b', MAX_BODY), false))
                .andExpect(status().isCreated());
    }

    @Test
    void bodyOneCharacterOverTheLimitIsRejected() throws Exception {
        mockMvc.perform(postAnnouncement("title", repeat('b', MAX_BODY + 1), false))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aSingleCharacterTitleAndBodyAreAccepted() throws Exception {
        mockMvc.perform(postAnnouncement("t", "b", false)).andExpect(status().isCreated());
    }

    // -------------------------------------------------------------------- read

    @Test
    void getReturnsTheRequestedAnnouncement() throws Exception {
        long id = createAnnouncement("Readable", "Body", false);

        mockMvc.perform(get("/api/announcements/" + id))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(id))
                .andExpect(jsonPath("$.title").value("Readable"));
    }

    @Test
    void getAnUnknownIdIs404() throws Exception {
        mockMvc.perform(get("/api/announcements/" + Long.MAX_VALUE))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("Not Found"));
    }

    @Test
    void getANegativeIdIs404() throws Exception {
        mockMvc.perform(get("/api/announcements/-1")).andExpect(status().isNotFound());
    }

    @Test
    void getAZeroIdIs404() throws Exception {
        mockMvc.perform(get("/api/announcements/0")).andExpect(status().isNotFound());
    }

    @Test
    void getANonNumericIdIs400() throws Exception {
        mockMvc.perform(get("/api/announcements/not-a-number")).andExpect(status().isBadRequest());
    }

    @Test
    void getAnIdBeyondLongRangeIs400() throws Exception {
        mockMvc.perform(get("/api/announcements/9223372036854775808"))
                .andExpect(status().isBadRequest());
    }

    // -------------------------------------------------------------------- list

    @Test
    void listDefaultsToPublishedOnly() throws Exception {
        createAnnouncement("Hidden draft " + uniqueSuffix(), "draft body", false);
        long publishedId = createAnnouncement("Visible " + uniqueSuffix(), "published body", true);

        JsonNode listed = readArray(get("/api/announcements"));

        org.junit.jupiter.api.Assertions.assertTrue(containsId(listed, publishedId));
        org.junit.jupiter.api.Assertions.assertTrue(allPublished(listed));
    }

    @Test
    void listWithPublishedOnlyFalseIncludesDrafts() throws Exception {
        long draftId = createAnnouncement("Draft " + uniqueSuffix(), "draft body", false);

        JsonNode listed = readArray(get("/api/announcements").param("publishedOnly", "false"));

        org.junit.jupiter.api.Assertions.assertTrue(containsId(listed, draftId));
    }

    @Test
    void listWithPublishedOnlyTrueExcludesDrafts() throws Exception {
        long draftId = createAnnouncement("Draft " + uniqueSuffix(), "draft body", false);

        JsonNode listed = readArray(get("/api/announcements").param("publishedOnly", "true"));

        org.junit.jupiter.api.Assertions.assertFalse(containsId(listed, draftId));
    }

    @Test
    void listRejectsANonBooleanFlag() throws Exception {
        mockMvc.perform(get("/api/announcements").param("publishedOnly", "maybe"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void listAcceptsTheAlternateBooleanSpellings() throws Exception {
        mockMvc.perform(get("/api/announcements").param("publishedOnly", "0")).andExpect(status().isOk());
        mockMvc.perform(get("/api/announcements").param("publishedOnly", "1")).andExpect(status().isOk());
    }

    // ----------------------------------------------------------------- publish

    @Test
    void publishFlipsADraftToPublished() throws Exception {
        long id = createAnnouncement("To publish", "body", false);

        mockMvc.perform(post("/api/announcements/" + id + "/publish"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(id))
                .andExpect(jsonPath("$.published").value(true));
    }

    @Test
    void publishIsIdempotent() throws Exception {
        long id = createAnnouncement("Publish twice", "body", false);

        mockMvc.perform(post("/api/announcements/" + id + "/publish")).andExpect(status().isOk());
        mockMvc.perform(post("/api/announcements/" + id + "/publish"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.published").value(true));
    }

    @Test
    void publishDoesNotChangeTheCreationTimestamp() throws Exception {
        long id = createAnnouncement("Timestamp stable", "body", false);
        String createdAt =
                readJson(get("/api/announcements/" + id)).get("createdAt").asText();

        mockMvc.perform(post("/api/announcements/" + id + "/publish"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.createdAt").value(createdAt));
    }

    @Test
    void publishAnUnknownIdIs404() throws Exception {
        mockMvc.perform(post("/api/announcements/" + Long.MAX_VALUE + "/publish"))
                .andExpect(status().isNotFound());
    }

    @Test
    void publishANonNumericIdIs400() throws Exception {
        mockMvc.perform(post("/api/announcements/abc/publish")).andExpect(status().isBadRequest());
    }

    @Test
    void getOnThePublishRouteIsNotAllowed() throws Exception {
        long id = createAnnouncement("Wrong verb", "body", false);

        mockMvc.perform(get("/api/announcements/" + id + "/publish"))
                .andExpect(status().isMethodNotAllowed());
    }

    // ------------------------------------------------------------------- authz

    @Test
    void anUnauthenticatedCallerCanCurrentlyCreateAndPublish() throws Exception {
        // FINDING (documented, not fixed here): legacy-portal has no authentication layer at
        // all — no spring-security dependency, no filter, no caller identity on the write
        // routes — so an anonymous request can create an announcement and publish it to
        // every user of the portal.
        long id = createAnnouncement("Anonymous broadcast", "body", false);

        mockMvc.perform(post("/api/announcements/" + id + "/publish")).andExpect(status().isOk());
    }

    @Test
    @Disabled("FINDING: legacy-portal has no authentication — announcement writes should reject anonymous callers")
    void anUnauthenticatedCallerShouldNotBeAbleToPublish() throws Exception {
        long id = createAnnouncement("Should be protected", "body", false);

        mockMvc.perform(post("/api/announcements/" + id + "/publish"))
                .andExpect(status().isUnauthorized());
    }

    // ----------------------------------------------------------------- helpers

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder postAnnouncement(
            String title, String body, boolean published) throws Exception {
        String json =
                objectMapper
                        .createObjectNode()
                        .put("title", title)
                        .put("body", body)
                        .put("published", published)
                        .toString();
        return post("/api/announcements").contentType(MediaType.APPLICATION_JSON).content(json);
    }

    private long createAnnouncement(String title, String body, boolean published) throws Exception {
        String response =
                mockMvc.perform(postAnnouncement(title, body, published))
                        .andExpect(status().isCreated())
                        .andReturn()
                        .getResponse()
                        .getContentAsString();
        return objectMapper.readTree(response).get("id").asLong();
    }

    private JsonNode readJson(
            org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder request)
            throws Exception {
        return objectMapper.readTree(
                mockMvc.perform(request)
                        .andExpect(status().isOk())
                        .andReturn()
                        .getResponse()
                        .getContentAsString());
    }

    private JsonNode readArray(
            org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder request)
            throws Exception {
        return readJson(request);
    }

    private static boolean containsId(JsonNode array, long id) {
        for (JsonNode node : array) {
            if (node.get("id").asLong() == id) {
                return true;
            }
        }
        return false;
    }

    private static boolean allPublished(JsonNode array) {
        for (JsonNode node : array) {
            if (!node.get("published").asBoolean()) {
                return false;
            }
        }
        return true;
    }

    private static String repeat(char c, int times) {
        StringBuilder sb = new StringBuilder(times);
        for (int i = 0; i < times; i++) {
            sb.append(c);
        }
        return sb.toString();
    }

    private static String uniqueSuffix() {
        return java.util.UUID.randomUUID().toString().substring(0, 8);
    }
}
