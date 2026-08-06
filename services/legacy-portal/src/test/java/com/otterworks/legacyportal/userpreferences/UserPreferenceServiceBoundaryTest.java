package com.otterworks.legacyportal.userpreferences;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.stream.Collectors;
import java.util.stream.IntStream;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;

/**
 * Persistence-boundary and idempotency cases for {@link UserPreferenceService} (WP-12).
 */
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import(UserPreferenceService.class)
class UserPreferenceServiceBoundaryTest {

    @Autowired private UserPreferenceService service;
    @Autowired private UserPreferenceRepository repository;

    private static String repeat(char c, int length) {
        return IntStream.range(0, length).mapToObj(i -> String.valueOf(c)).collect(Collectors.joining());
    }

    @Test
    @DisplayName("reading defaults does not silently create a row")
    void getOrDefaultIsAPureRead() {
        service.getOrDefault("ghost");
        service.getOrDefault("ghost");

        assertThat(repository.findById("ghost")).isEmpty();
        assertThat(repository.count()).isZero();
    }

    @Test
    void theDefaultsAreLightEnUsAndEmailOn() {
        UserPreference defaults = service.getOrDefault("ghost");

        assertThat(defaults.getUserId()).isEqualTo("ghost");
        assertThat(defaults.getTheme()).isEqualTo(UserPreferenceService.DEFAULT_THEME);
        assertThat(defaults.getLocale()).isEqualTo(UserPreferenceService.DEFAULT_LOCALE);
        assertThat(defaults.isEmailNotifications()).isTrue();
    }

    @Test
    @DisplayName("saving the same values twice is idempotent: one row, same content")
    void repeatedSavesDoNotAccumulateRows() {
        service.save("user-1", "dark", "fr-FR", false);
        service.save("user-1", "dark", "fr-FR", false);
        service.save("user-1", "dark", "fr-FR", false);

        assertThat(repository.count()).isEqualTo(1);
        UserPreference stored = service.getOrDefault("user-1");
        assertThat(stored.getTheme()).isEqualTo("dark");
        assertThat(stored.isEmailNotifications()).isFalse();
    }

    @Test
    void aSecondSaveOverwritesEveryFieldRatherThanMerging() {
        service.save("user-1", "dark", "fr-FR", false);
        service.save("user-1", "light", "en-GB", true);

        UserPreference stored = service.getOrDefault("user-1");
        assertThat(stored.getTheme()).isEqualTo("light");
        assertThat(stored.getLocale()).isEqualTo("en-GB");
        assertThat(stored.isEmailNotifications()).isTrue();
    }

    @Test
    void twoUsersPreferencesAreIndependent() {
        service.save("user-1", "dark", "fr-FR", false);
        service.save("user-2", "light", "en-US", true);

        assertThat(service.getOrDefault("user-1").getTheme()).isEqualTo("dark");
        assertThat(service.getOrDefault("user-2").getTheme()).isEqualTo("light");
        assertThat(repository.count()).isEqualTo(2);
    }

    @Test
    @DisplayName("userId and theme at the column-length boundary are persisted")
    void valuesAtTheColumnLengthLimitRoundTrip() {
        String maxUserId = repeat('u', 100);
        String maxTheme = repeat('t', 20);
        String maxLocale = repeat('l', 20);

        service.save(maxUserId, maxTheme, maxLocale, true);

        UserPreference stored = service.getOrDefault(maxUserId);
        assertThat(stored.getTheme()).isEqualTo(maxTheme);
        assertThat(stored.getLocale()).isEqualTo(maxLocale);
    }

    @Test
    void theServiceLayerAcceptsValuesTheWebLayerWouldReject() {
        // Negative case: @NotBlank/@Size live on the web DTO only, so a non-HTTP
        // caller can store a blank theme. Documented, not endorsed.
        service.save("user-1", "", "", false);

        assertThat(service.getOrDefault("user-1").getTheme()).isEmpty();
    }
}
