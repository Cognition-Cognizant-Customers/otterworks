package com.otterworks.legacyportal.userpreferences;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/preferences")
public class UserPreferenceController {

    private final UserPreferenceService service;

    public UserPreferenceController(UserPreferenceService service) {
        this.service = service;
    }

    @GetMapping("/{userId}")
    public PreferenceResponse get(@PathVariable String userId) {
        return PreferenceResponse.from(service.getOrDefault(userId));
    }

    @PutMapping("/{userId}")
    public PreferenceResponse update(
            @PathVariable String userId, @Valid @RequestBody UpdatePreferenceRequest request) {
        return PreferenceResponse.from(
                service.save(
                        userId,
                        request.getTheme(),
                        request.getLocale(),
                        request.isEmailNotifications()));
    }

    public static class UpdatePreferenceRequest {

        @NotBlank
        @Size(max = 20)
        private String theme;

        @NotBlank
        @Size(max = 20)
        private String locale;

        private boolean emailNotifications;

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

    public static class PreferenceResponse {

        private final String userId;
        private final String theme;
        private final String locale;
        private final boolean emailNotifications;

        private PreferenceResponse(
                String userId, String theme, String locale, boolean emailNotifications) {
            this.userId = userId;
            this.theme = theme;
            this.locale = locale;
            this.emailNotifications = emailNotifications;
        }

        static PreferenceResponse from(UserPreference p) {
            return new PreferenceResponse(
                    p.getUserId(), p.getTheme(), p.getLocale(), p.isEmailNotifications());
        }

        public String getUserId() {
            return userId;
        }

        public String getTheme() {
            return theme;
        }

        public String getLocale() {
            return locale;
        }

        public boolean isEmailNotifications() {
            return emailNotifications;
        }
    }
}
