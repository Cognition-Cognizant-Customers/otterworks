package com.otterworks.portal.preferences;

/** Mirrors the monolith's UserPreference entity/JSON shape. */
public class UserPreference {

    private String userId;
    private String theme;
    private String locale;
    private boolean emailNotifications;

    public UserPreference() {}

    public UserPreference(String userId, String theme, String locale, boolean emailNotifications) {
        this.userId = userId;
        this.theme = theme;
        this.locale = locale;
        this.emailNotifications = emailNotifications;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public String getTheme() {
        return theme;
    }

    public void setTheme(String theme) {
        this.theme = theme;
    }

    public String getLocale() {
        return locale;
    }

    public void setLocale(String locale) {
        this.locale = locale;
    }

    public boolean isEmailNotifications() {
        return emailNotifications;
    }

    public void setEmailNotifications(boolean emailNotifications) {
        this.emailNotifications = emailNotifications;
    }
}
