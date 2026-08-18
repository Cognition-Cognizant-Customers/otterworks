package com.otterworks.report.deps;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.otterworks.report.service.ReportHeaderRenderer;
import org.junit.Test;

import java.io.File;
import java.io.IOException;
import java.io.PrintWriter;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;

import static org.junit.Assume.assumeTrue;

/**
 * Emits the observed interpolation transcript for this module.
 *
 * Driven by the dependency harness (security/deps), never by a plain `mvn test`:
 * without -Dow.deps.cases / -Dow.deps.observed the test skips itself.
 *
 *   mvn -B test -Dtest=DependencyTranscriptEmitterTest \
 *       -Dow.deps.cases=<abs case file> -Dow.deps.observed=<abs output file>
 *
 * The emitter records outcomes; it does not judge them. Grading against the
 * recorded expectations is the harness's job so that one comparator governs
 * every module regardless of language.
 */
public class DependencyTranscriptEmitterTest {

    @Test
    public void emitTranscript() throws IOException {
        String casesPath = System.getProperty("ow.deps.cases");
        String observedPath = System.getProperty("ow.deps.observed");
        assumeTrue("dependency transcript not requested", casesPath != null && observedPath != null);

        ObjectMapper mapper = new ObjectMapper();
        JsonNode spec = mapper.readTree(new File(casesPath));
        ReportHeaderRenderer renderer = new ReportHeaderRenderer(new Properties());

        ObjectNode out = mapper.createObjectNode();
        out.put("module", spec.get("module").asText());
        ArrayNode observed = out.putArray("cases");

        for (JsonNode testCase : spec.get("cases")) {
            ObjectNode record = observed.addObject();
            record.put("id", testCase.get("id").asText());
            try {
                record.put("outcome", "ok");
                record.put("value", render(renderer, testCase));
            } catch (Throwable failure) {
                record.put("outcome", "error");
                record.put("error_type", failure.getClass().getName());
                record.put("error_message", String.valueOf(failure.getMessage()));
                record.remove("value");
            }
        }

        File outputFile = new File(observedPath);
        if (outputFile.getParentFile() != null) {
            outputFile.getParentFile().mkdirs();
        }
        PrintWriter writer = new PrintWriter(outputFile, "UTF-8");
        try {
            writer.print(mapper.writerWithDefaultPrettyPrinter().writeValueAsString(out));
        } finally {
            writer.close();
        }
    }

    private String render(ReportHeaderRenderer renderer, JsonNode testCase) throws IOException {
        String template = withFixture(testCase, testCase.get("template").asText());
        String kind = testCase.get("kind").asText();
        if ("banner".equals(kind)) {
            return renderer.renderBanner(template, variables(testCase));
        }
        if ("configured".equals(kind)) {
            return renderer.resolveConfigured(template);
        }
        throw new IllegalArgumentException("unsupported case kind: " + kind);
    }

    /**
     * Materialise a local file for cases that probe file-reading lookups, so the
     * recorded template stays machine independent.
     */
    private String withFixture(JsonNode testCase, String template) throws IOException {
        JsonNode content = testCase.get("fixture_content");
        if (content == null) {
            return template;
        }
        File fixture = File.createTempFile("ow-deps-fixture", ".txt");
        fixture.deleteOnExit();
        PrintWriter writer = new PrintWriter(fixture, "UTF-8");
        try {
            writer.print(content.asText());
        } finally {
            writer.close();
        }
        return template
                .replace("@FIXTURE_URL@", fixture.toURI().toString())
                .replace("@FIXTURE_PATH@", fixture.getAbsolutePath());
    }

    private Map<String, String> variables(JsonNode testCase) {
        Map<String, String> vars = new LinkedHashMap<String, String>();
        JsonNode node = testCase.get("vars");
        if (node == null) {
            return vars;
        }
        Iterator<String> names = node.fieldNames();
        while (names.hasNext()) {
            String name = names.next();
            vars.put(name, node.get(name).asText());
        }
        return vars;
    }
}
