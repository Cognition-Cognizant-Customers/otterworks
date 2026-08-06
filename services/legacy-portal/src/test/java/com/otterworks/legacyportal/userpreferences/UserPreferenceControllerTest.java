package com.otterworks.legacyportal.userpreferences;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import javax.persistence.EntityManager;
import javax.persistence.PersistenceContext;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

/**
 * Endpoint-level cases for {@link UserPreferenceController}: defaulting, round-tripping, the
 * declared length limits and cross-user access.
 *
 * <p>{@code @Transactional} keeps the shared in-memory H2 instance clean for the other modules'
 * suites; each test also uses its own user id so nothing depends on execution order.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class UserPreferenceControllerTest {

    private static final int MAX_THEME = 20;
    private static final int MAX_LOCALE = 20;
    private static final int MAX_USER_ID = 100;

    @Autowired private MockMvc mockMvc;
    @Autowired private ObjectMapper objectMapper;

    @PersistenceContext private EntityManager entityManager;

    // ---------------------------------------------------------------- defaults

    @Test
    void anUnknownUserGetsTheDocumentedDefaults() throws Exception {
        mockMvc.perform(get("/api/preferences/pref-unknown-user"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("pref-unknown-user"))
                .andExpect(jsonPath("$.theme").value("light"))
                .andExpect(jsonPath("$.locale").value("en-US"))
                .andExpect(jsonPath("$.emailNotifications").value(true));
    }

    @Test
    void readingDefaultsDoesNotPersistThem() throws Exception {
        mockMvc.perform(get("/api/preferences/pref-not-persisted")).andExpect(status().isOk());

        mockMvc.perform(get("/api/preferences/pref-not-persisted"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("light"));
    }

    // -------------------------------------------------------------- round trip

    @Test
    void updateReturnsTheStoredPreference() throws Exception {
        mockMvc.perform(putPreference("pref-round-trip", "dark", "fr-FR", false))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("pref-round-trip"))
                .andExpect(jsonPath("$.theme").value("dark"))
                .andExpect(jsonPath("$.locale").value("fr-FR"))
                .andExpect(jsonPath("$.emailNotifications").value(false));
    }

    @Test
    void anUpdateIsVisibleToTheNextRead() throws Exception {
        mockMvc.perform(putPreference("pref-visible", "dark", "de-DE", true)).andExpect(status().isOk());

        mockMvc.perform(get("/api/preferences/pref-visible"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("dark"))
                .andExpect(jsonPath("$.locale").value("de-DE"));
    }

    @Test
    void repeatingTheSameUpdateIsIdempotent() throws Exception {
        mockMvc.perform(putPreference("pref-idempotent", "dark", "en-GB", false)).andExpect(status().isOk());
        mockMvc.perform(putPreference("pref-idempotent", "dark", "en-GB", false))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("dark"))
                .andExpect(jsonPath("$.locale").value("en-GB"))
                .andExpect(jsonPath("$.emailNotifications").value(false));
    }

    @Test
    void aSecondUpdateOverwritesTheFirst() throws Exception {
        mockMvc.perform(putPreference("pref-overwrite", "dark", "en-GB", false)).andExpect(status().isOk());

        mockMvc.perform(putPreference("pref-overwrite", "light", "en-US", true))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("light"))
                .andExpect(jsonPath("$.emailNotifications").value(true));
    }

    @Test
    void omittingTheNotificationFlagTurnsNotificationsOff() throws Exception {
        // FINDING (documented, not fixed here): emailNotifications is a primitive boolean on
        // the request DTO with no @NotNull, so a partial PUT that leaves it out silently
        // opts the user out of email — the stored value flips from the true default to false.
        mockMvc.perform(
                        put("/api/preferences/pref-partial")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"theme\":\"dark\",\"locale\":\"en-US\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.emailNotifications").value(false));
    }

    // ------------------------------------------------------- length boundaries

    @Test
    void aThemeOneCharacterUnderTheLimitIsAccepted() throws Exception {
        mockMvc.perform(putPreference("pref-theme-len", repeat('t', MAX_THEME - 1), "en-US", true))
                .andExpect(status().isOk());
    }

    @Test
    void aThemeExactlyAtTheLimitIsAccepted() throws Exception {
        mockMvc.perform(putPreference("pref-theme-len", repeat('t', MAX_THEME), "en-US", true))
                .andExpect(status().isOk());
    }

    @Test
    void aThemeOneCharacterOverTheLimitIsRejected() throws Exception {
        mockMvc.perform(putPreference("pref-theme-len", repeat('t', MAX_THEME + 1), "en-US", true))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aLocaleOneCharacterUnderTheLimitIsAccepted() throws Exception {
        mockMvc.perform(putPreference("pref-locale-len", "dark", repeat('l', MAX_LOCALE - 1), true))
                .andExpect(status().isOk());
    }

    @Test
    void aLocaleExactlyAtTheLimitIsAccepted() throws Exception {
        mockMvc.perform(putPreference("pref-locale-len", "dark", repeat('l', MAX_LOCALE), true))
                .andExpect(status().isOk());
    }

    @Test
    void aLocaleOneCharacterOverTheLimitIsRejected() throws Exception {
        mockMvc.perform(putPreference("pref-locale-len", "dark", repeat('l', MAX_LOCALE + 1), true))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aSingleCharacterThemeAndLocaleAreAccepted() throws Exception {
        mockMvc.perform(putPreference("pref-single-char", "d", "e", true)).andExpect(status().isOk());
    }

    // ------------------------------------------------------ user id boundaries

    @Test
    void aUserIdOneCharacterUnderTheColumnLimitIsStored() throws Exception {
        String userId = repeat('u', MAX_USER_ID - 1);

        mockMvc.perform(putPreference(userId, "dark", "en-US", true))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value(userId));
    }

    @Test
    void aUserIdExactlyAtTheColumnLimitIsStored() throws Exception {
        String userId = repeat('u', MAX_USER_ID);

        mockMvc.perform(putPreference(userId, "dark", "en-US", true))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value(userId));
    }

    @Test
    void aUserIdOverTheColumnLimitIsOnlyCaughtWhenTheWriteReachesTheDatabase() throws Exception {
        // FINDING (documented, not fixed here): the {userId} path variable carries no @Size
        // and the controller class is not @Validated, so an id longer than the 100-character
        // column passes validation and the request answers 200 OK. The violation only
        // surfaces when the write is flushed, which in production happens after the response
        // has been committed.
        String userId = repeat('u', MAX_USER_ID + 1);

        mockMvc.perform(putPreference(userId, "dark", "en-US", true)).andExpect(status().isOk());

        Assertions.assertThrows(Exception.class, () -> entityManager.flush());
    }

    @Test
    void anOverLongUserIdStillReadsBackDefaults() throws Exception {
        mockMvc.perform(get("/api/preferences/" + repeat('u', MAX_USER_ID + 1)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("light"));
    }

    // -------------------------------------------------------- malformed update

    @Test
    void updateRejectsAnEmptyBody() throws Exception {
        mockMvc.perform(put("/api/preferences/pref-empty").contentType(MediaType.APPLICATION_JSON).content(""))
                .andExpect(status().isBadRequest());
    }

    @Test
    void updateRejectsBrokenJson() throws Exception {
        mockMvc.perform(
                        put("/api/preferences/pref-broken")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"theme\":"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void updateRejectsAMissingContentType() throws Exception {
        mockMvc.perform(put("/api/preferences/pref-no-ct").content("{\"theme\":\"dark\",\"locale\":\"en-US\"}"))
                .andExpect(status().isUnsupportedMediaType());
    }

    @Test
    void updateRejectsABlankTheme() throws Exception {
        mockMvc.perform(putPreference("pref-blank-theme", "   ", "en-US", true))
                .andExpect(status().isBadRequest());
    }

    @Test
    void updateRejectsAMissingTheme() throws Exception {
        mockMvc.perform(
                        put("/api/preferences/pref-missing-theme")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"locale\":\"en-US\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void updateRejectsABlankLocale() throws Exception {
        mockMvc.perform(putPreference("pref-blank-locale", "dark", "", true))
                .andExpect(status().isBadRequest());
    }

    @Test
    void updateRejectsAWronglyTypedFlag() throws Exception {
        mockMvc.perform(
                        put("/api/preferences/pref-bad-flag")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"theme\":\"dark\",\"locale\":\"en-US\",\"emailNotifications\":\"sometimes\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void aRejectedUpdateLeavesThePreviousValueInPlace() throws Exception {
        mockMvc.perform(putPreference("pref-unchanged", "dark", "en-GB", false)).andExpect(status().isOk());

        mockMvc.perform(putPreference("pref-unchanged", "  ", "en-GB", false))
                .andExpect(status().isBadRequest());

        mockMvc.perform(get("/api/preferences/pref-unchanged"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("dark"));
    }

    @Test
    void anyThemeStringIsAcceptedIncludingUnknownOnes() throws Exception {
        // There is no allow-list behind @Size, so "solarized-mauve" is as valid as "dark".
        mockMvc.perform(putPreference("pref-any-theme", "solarized", "xx-YY", true))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("solarized"));
    }

    @Test
    void deletingAPreferenceIsNotSupported() throws Exception {
        mockMvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete(
                        "/api/preferences/pref-delete"))
                .andExpect(status().isMethodNotAllowed());
    }

    // ------------------------------------------------------------------- authz

    @Test
    void anyCallerCanCurrentlyReadAnotherUsersPreferences() throws Exception {
        // FINDING (documented, not fixed here): the owner is taken from the path and
        // legacy-portal has no authentication layer, so any caller can read — and overwrite —
        // any other user's preferences.
        mockMvc.perform(putPreference("pref-owner-a", "dark", "en-GB", false)).andExpect(status().isOk());

        mockMvc.perform(get("/api/preferences/pref-owner-a").header("X-User-ID", "pref-owner-b"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("dark"));
    }

    @Test
    void anyCallerCanCurrentlyOverwriteAnotherUsersPreferences() throws Exception {
        mockMvc.perform(putPreference("pref-victim", "dark", "en-GB", false)).andExpect(status().isOk());

        mockMvc.perform(
                        put("/api/preferences/pref-victim")
                                .header("X-User-ID", "pref-attacker")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"theme\":\"light\",\"locale\":\"en-US\",\"emailNotifications\":false}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("light"));
    }

    @Test
    @Disabled("FINDING: legacy-portal has no authentication — writing another user's preferences should be rejected")
    void writingAnotherUsersPreferencesShouldBeRejected() throws Exception {
        mockMvc.perform(putPreference("pref-victim-2", "dark", "en-GB", false)).andExpect(status().isOk());

        mockMvc.perform(
                        put("/api/preferences/pref-victim-2")
                                .header("X-User-ID", "pref-attacker-2")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content("{\"theme\":\"light\",\"locale\":\"en-US\",\"emailNotifications\":false}"))
                .andExpect(status().isForbidden());
    }

    @Test
    void preferencesAreKeyedCaseSensitively() throws Exception {
        mockMvc.perform(putPreference("pref-Case", "dark", "en-GB", false)).andExpect(status().isOk());

        mockMvc.perform(get("/api/preferences/pref-case"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.theme").value("light"));
    }

    // ----------------------------------------------------------------- helpers

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder putPreference(
            String userId, String theme, String locale, boolean emailNotifications) {
        String json =
                objectMapper
                        .createObjectNode()
                        .put("theme", theme)
                        .put("locale", locale)
                        .put("emailNotifications", emailNotifications)
                        .toString();
        return put("/api/preferences/" + userId)
                .contentType(MediaType.APPLICATION_JSON)
                .content(json);
    }

    private static String repeat(char c, int times) {
        StringBuilder sb = new StringBuilder(times);
        for (int i = 0; i < times; i++) {
            sb.append(c);
        }
        return sb.toString();
    }
}
