package com.otterworks.legacyportal.userpreferences;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * Bounded context: user-preferences. Owns the {@code user_preferences} schema.
 */
@Entity
@Table(name = "user_preference", schema = "user_preferences")
public class UserPreference {

    @Id
    @Column(name = "user_id", length = 100)
    private String userId;

    @Column(nullable = false, length = 20)
    private String theme;

    @Column(nullable = false, length = 20)
    private String locale;

    @Column(name = "email_notifications", nullable = false)
    private boolean emailNotifications;

    protected UserPreference() {
        // JPA
    }

    public UserPreference(String userId, String theme, String locale, boolean emailNotifications) {
        this.userId = userId;
        this.theme = theme;
        this.locale = locale;
        this.emailNotifications = emailNotifications;
    }

    public String getUserId() {
        return userId;
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
