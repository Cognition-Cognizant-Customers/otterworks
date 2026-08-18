package com.otterworks.legacyportal.common;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** Unit tests for the interpolated portal branding settings. */
class PortalBrandingSettingsTest {

    private PortalBrandingSettings settings;

    @BeforeEach
    void setUp() throws Exception {
        settings = new PortalBrandingSettings();
        settings.load();
    }

    @Test
    void bannerResolvesOtherSettingsKeys() {
        assertEquals(
                "OtterWorks Portal (on-prem) - contact portal-support@otterworks.example",
                settings.bannerText());
    }

    @Test
    void supportContactIsReadDirectly() {
        assertEquals("portal-support@otterworks.example", settings.supportContact());
    }
}
