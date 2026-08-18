package com.otterworks.legacyportal.common;

import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Lightweight health endpoint matching the {@code /health} convention used by the other OtterWorks
 * services (Actuator's {@code /actuator/health} is also enabled).
 */
@RestController
public class HealthController {

    private final PortalBrandingSettings branding;

    public HealthController(PortalBrandingSettings branding) {
        this.branding = branding;
    }

    @GetMapping("/health")
    public Map<String, String> health() {
        Map<String, String> body = new LinkedHashMap<>();
        body.put("status", "UP");
        body.put("service", "legacy-portal");
        body.put("banner", branding.bannerText());
        return body;
    }
}
