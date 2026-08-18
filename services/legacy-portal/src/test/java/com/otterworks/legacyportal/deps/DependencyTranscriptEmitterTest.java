package com.otterworks.legacyportal.deps;

import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.otterworks.legacyportal.common.PortalBrandingSettings;
import java.io.File;
import java.io.IOException;
import java.io.PrintWriter;
import org.junit.jupiter.api.Test;

/**
 * Emits the observed interpolation transcript for this module.
 *
 * <p>Driven by the dependency harness (security/deps): without -Dow.deps.cases and
 * -Dow.deps.observed it skips itself, so a plain {@code mvn test} is unaffected. The
 * harness grades the result, so one comparator governs every module.
 */
class DependencyTranscriptEmitterTest {

    @Test
    void emitTranscript() throws Exception {
        String casesPath = System.getProperty("ow.deps.cases");
        String observedPath = System.getProperty("ow.deps.observed");
        assumeTrue(casesPath != null && observedPath != null, "dependency transcript not requested");

        ObjectMapper mapper = new ObjectMapper();
        JsonNode spec = mapper.readTree(new File(casesPath));
        PortalBrandingSettings settings = new PortalBrandingSettings();
        settings.load();

        ObjectNode out = mapper.createObjectNode();
        out.put("module", spec.get("module").asText());
        ArrayNode observed = out.putArray("cases");

        for (JsonNode testCase : spec.get("cases")) {
            ObjectNode record = observed.addObject();
            record.put("id", testCase.get("id").asText());
            try {
                String value = render(settings, testCase);
                record.put("outcome", "ok");
                record.put("value", value);
            } catch (Throwable failure) {
                record.put("outcome", "error");
                record.put("error_type", failure.getClass().getName());
                record.put("error_message", String.valueOf(failure.getMessage()));
            }
        }

        File outputFile = new File(observedPath);
        if (outputFile.getParentFile() != null) {
            outputFile.getParentFile().mkdirs();
        }
        try (PrintWriter writer = new PrintWriter(outputFile, "UTF-8")) {
            writer.print(mapper.writerWithDefaultPrettyPrinter().writeValueAsString(out));
        }
    }

    private String render(PortalBrandingSettings settings, JsonNode testCase) throws IOException {
        String kind = testCase.get("kind").asText();
        if ("settings-key".equals(kind)) {
            String key = testCase.get("key").asText();
            if ("portal.banner".equals(key)) {
                return settings.bannerText();
            }
            if ("portal.support".equals(key)) {
                return settings.supportContact();
            }
            throw new IllegalArgumentException("unsupported settings key: " + key);
        }
        if ("configured".equals(kind)) {
            return settings.interpolate(withFixture(testCase, testCase.get("template").asText()));
        }
        throw new IllegalArgumentException("unsupported case kind: " + kind);
    }

    /**
     * Materialise a local file for cases that probe file-reading lookups, so the recorded
     * template stays machine independent.
     */
    private String withFixture(JsonNode testCase, String template) throws IOException {
        JsonNode content = testCase.get("fixture_content");
        if (content == null) {
            return template;
        }
        File fixture = File.createTempFile("ow-deps-fixture", ".txt");
        fixture.deleteOnExit();
        try (PrintWriter writer = new PrintWriter(fixture, "UTF-8")) {
            writer.print(content.asText());
        }
        return template
                .replace("@FIXTURE_URL@", fixture.toURI().toString())
                .replace("@FIXTURE_PATH@", fixture.getAbsolutePath());
    }
}
