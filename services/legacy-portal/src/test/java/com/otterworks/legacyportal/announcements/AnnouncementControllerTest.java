package com.otterworks.legacyportal.announcements;

import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.willThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.Arrays;
import java.util.Collections;
import java.util.NoSuchElementException;
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
 * Web-layer cases for the previously untested {@link AnnouncementController} (WP-12):
 * request-parameter defaulting, the {@code @Size} boundaries on the create payload,
 * the not-found mapping supplied by the global advice, and the missing authorization
 * on the write endpoints.
 */
@WebMvcTest(AnnouncementController.class)
class AnnouncementControllerTest {

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;

    @MockBean private AnnouncementService service;

    private static Announcement announcement(String title, String body, boolean published) {
        return new Announcement(title, body, published);
    }

    private String createBody(String title, String body, boolean published) throws Exception {
        ObjectNode node = objectMapper.createObjectNode();
        node.put("title", title);
        node.put("body", body);
        node.put("published", published);
        return objectMapper.writeValueAsString(node);
    }

    private static String repeat(char c, int length) {
        return IntStream.range(0, length).mapToObj(i -> String.valueOf(c)).collect(Collectors.joining());
    }

    // ---- list ----

    @Test
    @DisplayName("list defaults to published-only")
    void listDefaultsToPublishedOnly() throws Exception {
        given(service.listPublished()).willReturn(Arrays.asList(announcement("live", "body", true)));

        mockMvc.perform(get("/api/announcements"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].title").value("live"))
                .andExpect(jsonPath("$[0].published").value(true));

        verify(service, never()).listAll();
    }

    @Test
    void listWithPublishedOnlyFalseIncludesDrafts() throws Exception {
        given(service.listAll())
                .willReturn(Arrays.asList(announcement("draft", "body", false), announcement("live", "body", true)));

        mockMvc.perform(get("/api/announcements").param("publishedOnly", "false"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2));

        verify(service, never()).listPublished();
    }

    @Test
    void listReturnsAnEmptyArrayRatherThanA404() throws Exception {
        given(service.listPublished()).willReturn(Collections.emptyList());

        mockMvc.perform(get("/api/announcements"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void listRejectsANonBooleanPublishedOnlyFlag() throws Exception {
        mockMvc.perform(get("/api/announcements").param("publishedOnly", "maybe"))
                .andExpect(status().isBadRequest());
    }

    // ---- get ----

    @Test
    void getReturnsTheAnnouncement() throws Exception {
        given(service.get(1L)).willReturn(announcement("live", "hello", true));

        mockMvc.perform(get("/api/announcements/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.title").value("live"))
                .andExpect(jsonPath("$.body").value("hello"));
    }

    @Test
    void getOfAnUnknownIdIsMappedToA404WithAMessage() throws Exception {
        willThrow(new NoSuchElementException("announcement 999 not found")).given(service).get(999L);

        mockMvc.perform(get("/api/announcements/999"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("Not Found"))
                .andExpect(jsonPath("$.message").value("announcement 999 not found"));
    }

    @Test
    void getRejectsANonNumericId() throws Exception {
        mockMvc.perform(get("/api/announcements/not-a-number")).andExpect(status().isBadRequest());
        verify(service, never()).get(anyLong());
    }

    // ---- create: validation boundaries ----

    @Test
    void createReturns201AndDelegatesToTheService() throws Exception {
        given(service.create(anyString(), anyString(), anyBoolean()))
                .willReturn(announcement("Release notes", "body", true));

        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(createBody("Release notes", "body", true)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.title").value("Release notes"));

        verify(service).create("Release notes", "body", true);
    }

    @Test
    void createDefaultsPublishedToFalseWhenItIsOmitted() throws Exception {
        given(service.create(anyString(), anyString(), anyBoolean()))
                .willReturn(announcement("Draft", "body", false));

        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"title\":\"Draft\",\"body\":\"body\"}"))
                .andExpect(status().isCreated());

        verify(service).create("Draft", "body", false);
    }

    @Test
    @DisplayName("title honours the 200-character @Size boundary trio")
    void titleLengthBoundaryTrio() throws Exception {
        given(service.create(anyString(), anyString(), anyBoolean()))
                .willReturn(announcement("t", "body", false));

        expectCreateStatus(repeat('t', 199), "body", 201);
        expectCreateStatus(repeat('t', 200), "body", 201);
        expectCreateStatus(repeat('t', 201), "body", 400);
    }

    @Test
    @DisplayName("body honours the 4000-character @Size boundary trio")
    void bodyLengthBoundaryTrio() throws Exception {
        given(service.create(anyString(), anyString(), anyBoolean()))
                .willReturn(announcement("title", "b", false));

        expectCreateStatus("title", repeat('b', 3999), 201);
        expectCreateStatus("title", repeat('b', 4000), 201);
        expectCreateStatus("title", repeat('b', 4001), 400);
    }

    private void expectCreateStatus(String title, String body, int expectedStatus) throws Exception {
        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(createBody(title, body, false)))
                .andExpect(status().is(expectedStatus));
    }

    @Test
    void createRejectsABlankOrMissingTitleAndBody() throws Exception {
        expectCreateStatus("   ", "body", 400);
        expectCreateStatus("title", "", 400);

        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"body\":\"orphan\"}"))
                .andExpect(status().isBadRequest());

        verify(service, never()).create(anyString(), anyString(), anyBoolean());
    }

    @Test
    void createRejectsMalformedJsonAndAnEmptyBody() throws Exception {
        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"title\":"))
                .andExpect(status().isBadRequest());

        mockMvc.perform(post("/api/announcements").contentType(MediaType.APPLICATION_JSON).content(""))
                .andExpect(status().isBadRequest());
    }

    @Test
    void createRejectsAWrongTypedPublishedFlag() throws Exception {
        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"title\":\"t\",\"body\":\"b\",\"published\":\"yes-please\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void createRejectsAnUnsupportedContentType() throws Exception {
        mockMvc.perform(post("/api/announcements").contentType(MediaType.TEXT_PLAIN).content("title=t"))
                .andExpect(status().isUnsupportedMediaType());
    }

    // ---- publish ----

    @Test
    void publishFlipsTheFlag() throws Exception {
        given(service.publish(1L)).willReturn(announcement("live", "body", true));

        mockMvc.perform(post("/api/announcements/1/publish"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.published").value(true));
    }

    @Test
    void publishOfAnUnknownIdIs404() throws Exception {
        willThrow(new NoSuchElementException("announcement 999 not found")).given(service).publish(999L);

        mockMvc.perform(post("/api/announcements/999/publish")).andExpect(status().isNotFound());
    }

    // ---- authorization ----

    @Test
    @DisplayName("authz negative: anyone can publish an announcement unauthenticated")
    void writeEndpointsAcceptUnauthenticatedCallers() throws Exception {
        // FINDING (WP-12, authz negative): legacy-portal has no security starter and
        // no method-level checks, so creating and publishing announcements -- content
        // shown to every portal user -- is open to anonymous callers. Pinned as
        // today's behaviour; adding authorization is a production change.
        given(service.create(anyString(), anyString(), anyBoolean()))
                .willReturn(announcement("Anyone can post", "body", true));
        given(service.publish(eq(1L))).willReturn(announcement("Anyone can publish", "body", true));

        mockMvc.perform(
                        post("/api/announcements")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(createBody("Anyone can post", "body", true)))
                .andExpect(status().isCreated());

        mockMvc.perform(post("/api/announcements/1/publish")).andExpect(status().isOk());
    }
}
