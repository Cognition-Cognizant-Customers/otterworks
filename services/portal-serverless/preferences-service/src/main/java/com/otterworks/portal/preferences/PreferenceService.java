package com.otterworks.portal.preferences;

/** Business rules mirroring the monolith's UserPreferenceService. */
public class PreferenceService {

    static final String DEFAULT_THEME = "light";
    static final String DEFAULT_LOCALE = "en-US";

    private final PreferenceStore store;

    public PreferenceService(PreferenceStore store) {
        this.store = store;
    }

    /** Returns stored preferences, or defaults if the user has none yet. */
    public UserPreference getOrDefault(String userId) {
        return store.find(userId)
                .orElseGet(() -> new UserPreference(userId, DEFAULT_THEME, DEFAULT_LOCALE, true));
    }

    public UserPreference save(String userId, String theme, String locale, boolean emailNotifications) {
        UserPreference preference = getOrDefault(userId);
        preference.setTheme(theme);
        preference.setLocale(locale);
        preference.setEmailNotifications(emailNotifications);
        store.put(preference);
        return preference;
    }
}
