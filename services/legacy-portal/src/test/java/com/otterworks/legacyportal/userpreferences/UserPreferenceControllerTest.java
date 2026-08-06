package com.otterworks.legacyportal.userpreferences;

import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
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
 * Web-layer cases for the previously untested {@link UserPreferenceController} (WP-12):
 * the default-preference read path, the {@code @Size(max = 20)} boundaries on theme and
 * locale, and the absent ownership check on the write path.
 */
@WebMvcTest(UserPreferenceController.class)
class UserPreferenceControllerTest {

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;

    @MockBean private UserPreferenceService service;

    private static String repeat(char c, int length) {
        return IntStream.range(0, length).mapToObj(i -> String.valueOf(c)).collect(Collectors.joining());
    }

    private String updateBody(String theme, String locale, boolean emailNotifications) throws Exception {
        return objectMapper.writeValueAsString(
                objectMapper
                        .createObjectNode()
                        .put("theme", theme)
                        .put("locale", locale)
                        .put("emailNotifications", emailNotifications));
    }

    private void expectUpdateStatus(String theme, String locale, int expectedStatus) throws Exception {
        mockMvc.perform(
                        put("/api/preferences/user-1")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(updateBody(theme, locale, true)))
                .andExpect(status().is(expectedStatus));
    }

    // ---- read ----

    @Test
    void getReturnsTheStoredPreference() throws Exception {
        given(service.getOrDefault("user-1")).willReturn(new UserPreference("user-1", "dark", "fr-FR", false));

        mockMvc.perform(get("/api/preferences/user-1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("user-1"))
                .andExpect(jsonPath("$.theme").value("dark"))
                .andExpect(jsonPath("$.locale").value("fr-FR"))
                .andExpect(jsonPath("$.emailNotifications").value(false));
    }

    @Test
    void getOfAnUnknownUserReturnsDefaultsRatherThanA404() throws Exception {
        given(service.getOrDefault("nobody")).willReturn(new UserPreference("nobody", "light", "en-US", true));

        mockMvc.perform(get("/api/preferences/nobody"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("light"))
                .andExpect(jsonPath("$.locale").value("en-US"))
                .andExpect(jsonPath("$.emailNotifications").value(true));
    }

    @Test
    void aUserIdContainingAPathSeparatorDoesNotMatchTheRoute() throws Exception {
        mockMvc.perform(get("/api/preferences/tenant/user-1")).andExpect(status().isNotFound());
        verify(service, never()).getOrDefault(anyString());
    }

    // ---- write: validation boundaries ----

    @Test
    void updatePersistsAndEchoesTheNewValues() throws Exception {
        given(service.save("user-1", "dark", "de-DE", false))
                .willReturn(new UserPreference("user-1", "dark", "de-DE", false));

        mockMvc.perform(
                        put("/api/preferences/user-1")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(updateBody("dark", "de-DE", false)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("dark"));

        verify(service).save("user-1", "dark", "de-DE", false);
    }

    @Test
    @DisplayName("theme honours the 20-character boundary trio")
    void themeLengthBoundaryTrio() throws Exception {
        given(service.save(anyString(), anyString(), anyString(), anyBoolean()))
                .willReturn(new UserPreference("user-1", "t", "en-US", true));

        expectUpdateStatus(repeat('t', 19), "en-US", 200);
        expectUpdateStatus(repeat('t', 20), "en-US", 200);
        expectUpdateStatus(repeat('t', 21), "en-US", 400);
    }

    @Test
    @DisplayName("locale honours the 20-character boundary trio")
    void localeLengthBoundaryTrio() throws Exception {
        given(service.save(anyString(), anyString(), anyString(), anyBoolean()))
                .willReturn(new UserPreference("user-1", "light", "l", true));

        expectUpdateStatus("light", repeat('l', 19), 200);
        expectUpdateStatus("light", repeat('l', 20), 200);
        expectUpdateStatus("light", repeat('l', 21), 400);
    }

    @Test
    void updateRejectsBlankOrMissingThemeAndLocale() throws Exception {
        expectUpdateStatus("   ", "en-US", 400);
        expectUpdateStatus("light", "", 400);

        mockMvc.perform(
                        put("/api/preferences/user-1")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"theme\":\"light\"}"))
                .andExpect(status().isBadRequest());

        verify(service, never()).save(anyString(), anyString(), anyString(), anyBoolean());
    }

    @Test
    void updateRejectsMalformedJson() throws Exception {
        mockMvc.perform(put("/api/preferences/user-1").contentType(MediaType.APPLICATION_JSON).content("{\"theme\""))
                .andExpect(status().isBadRequest());
    }

    @Test
    void updateAcceptsAnUnknownThemeValue() throws Exception {
        // Negative case: theme is a free-text column, so "chartreuse" is stored as-is.
        // There is no allow-list; a rendering bug is the only feedback a user gets.
        given(service.save(anyString(), anyString(), anyString(), anyBoolean()))
                .willReturn(new UserPreference("user-1", "chartreuse", "en-US", true));

        expectUpdateStatus("chartreuse", "en-US", 200);
    }

    @Test
    void updateDefaultsEmailNotificationsToFalseWhenOmitted() throws Exception {
        given(service.save(anyString(), anyString(), anyString(), anyBoolean()))
                .willReturn(new UserPreference("user-1", "light", "en-US", false));

        mockMvc.perform(
                        put("/api/preferences/user-1")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"theme\":\"light\",\"locale\":\"en-US\"}"))
                .andExpect(status().isOk());

        verify(service).save("user-1", "light", "en-US", false);
    }

    // ---- authorization ----

    @Test
    @DisplayName("authz negative: any caller can overwrite another user's preferences")
    void anotherUsersPreferencesAreWritableWithoutAuthentication() throws Exception {
        // FINDING (WP-12, authz negative): the userId comes from the path and is never
        // checked against an authenticated principal, so `PUT /api/preferences/{anyone}`
        // silently rewrites a stranger's settings. Pinned as today's behaviour.
        given(service.save("someone-else", "dark", "ru-RU", false))
                .willReturn(new UserPreference("someone-else", "dark", "ru-RU", false));

        mockMvc.perform(
                        put("/api/preferences/someone-else")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(updateBody("dark", "ru-RU", false)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("someone-else"));
    }

    @Test
    @DisplayName("authz negative: any caller can read another user's preferences")
    void anotherUsersPreferencesAreReadableWithoutAuthentication() throws Exception {
        given(service.getOrDefault("someone-else"))
                .willReturn(new UserPreference("someone-else", "dark", "ru-RU", false));

        mockMvc.perform(get("/api/preferences/someone-else"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.locale").value("ru-RU"));
    }
}
