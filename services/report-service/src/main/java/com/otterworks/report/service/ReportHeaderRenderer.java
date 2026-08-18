package com.otterworks.report.service;

import org.apache.commons.text.StringSubstitutor;
import org.apache.commons.text.lookup.StringLookupFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.InputStream;
import java.util.Map;
import java.util.Properties;

/**
 * Renders the operator-configurable metadata banner and footer that wrap every
 * exported report.
 *
 * LEGACY PATTERNS:
 * - Commons Text 1.9 (2020) string interpolation instead of a template engine
 * - Templates live in report-banner.properties, editable by operations without a
 *   redeploy, and are read with java.util.Properties so that the ${...} tokens are
 *   not eaten by Spring's own property placeholder resolution.
 *
 * The banner lines are rendered against per-report variables. The footer is an
 * operator string with no report variables, resolved against the default
 * interpolator lookups (branding blob, host metadata) — which is why the two
 * paths build their substitutors differently.
 */
@Component
public class ReportHeaderRenderer {

    private static final Logger logger = LoggerFactory.getLogger(ReportHeaderRenderer.class);

    private static final String BANNER_FILE = "report-banner.properties";

    /** Distribution classification stamped on every export; referenced by the footer template. */
    public static final String CLASSIFICATION = "INTERNAL USE ONLY";

    static final String DEFAULT_TITLE = "# OtterWorks Report: ${reportName}";
    static final String DEFAULT_GENERATED = "# Generated: ${generated}";
    static final String DEFAULT_PERIOD = "# Period: ${periodFrom} to ${periodTo}";
    static final String DEFAULT_ROWS = "# Rows: ${rows}";
    static final String DEFAULT_FOOTER = "# ${base64Decoder:R2VuZXJhdGVkIGJ5IE90dGVyV29ya3MgUmVwb3J0aW5n}"
            + " | ${const:com.otterworks.report.service.ReportHeaderRenderer.CLASSIFICATION}";

    private final Properties templates;

    public ReportHeaderRenderer() {
        this(loadTemplates());
    }

    public ReportHeaderRenderer(Properties templates) {
        this.templates = templates;
    }

    private static Properties loadTemplates() {
        Properties props = new Properties();
        InputStream in = ReportHeaderRenderer.class.getClassLoader().getResourceAsStream(BANNER_FILE);
        if (in == null) {
            return props;
        }
        try {
            props.load(in);
        } catch (IOException e) {
            logger.warn("Could not read {}, falling back to built-in banner templates", BANNER_FILE, e);
        } finally {
            try {
                in.close();
            } catch (IOException ignored) {
                // nothing useful to do on close failure
            }
        }
        return props;
    }

    /**
     * Render one banner line against the supplied report variables.
     *
     * An undefined variable is an operator error: it must fail loudly instead of
     * leaking a raw ${...} token into a customer-facing export. Report values are
     * inserted literally: a report whose name happens to contain ${...} is data,
     * not a template, so resolved values are never re-scanned.
     */
    public String renderBanner(String template, Map<String, String> vars) {
        StringSubstitutor substitutor =
                new StringSubstitutor(StringLookupFactory.INSTANCE.interpolatorStringLookup(vars));
        substitutor.setEnableUndefinedVariableException(true);
        substitutor.setDisableSubstitutionInValues(true);
        return substitutor.replace(template);
    }

    /**
     * Resolve an operator-supplied configuration string (branding, host metadata).
     * No report variables participate; the default interpolator lookups do.
     */
    public String resolveConfigured(String template) {
        return StringSubstitutor.createInterpolator().replace(template);
    }

    public String title(Map<String, String> vars) {
        return renderBanner(templates.getProperty("banner.title", DEFAULT_TITLE), vars);
    }

    public String generated(Map<String, String> vars) {
        return renderBanner(templates.getProperty("banner.generated", DEFAULT_GENERATED), vars);
    }

    public String period(Map<String, String> vars) {
        return renderBanner(templates.getProperty("banner.period", DEFAULT_PERIOD), vars);
    }

    public String rows(Map<String, String> vars) {
        return renderBanner(templates.getProperty("banner.rows", DEFAULT_ROWS), vars);
    }

    public String footer() {
        return resolveConfigured(templates.getProperty("banner.footer", DEFAULT_FOOTER));
    }
}
