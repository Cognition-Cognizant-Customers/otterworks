package com.otterworks.report.service;

import org.junit.Test;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

/**
 * Unit tests for {@link ReportHeaderRenderer}.
 *
 * Covers the per-report variable path (the banner lines). The operator footer
 * string is exercised by the dependency transcript in security/deps, which pins
 * the interpolation behavior of the Commons Text release we build against.
 *
 * JUnit 4 style to match the current stack.
 */
public class ReportHeaderRendererTest {

    private final ReportHeaderRenderer renderer = new ReportHeaderRenderer(new Properties());

    @Test
    public void rendersReportVariables() {
        assertEquals("# OtterWorks Report: Quarterly Usage",
                renderer.title(vars("reportName", "Quarterly Usage")));
    }

    @Test
    public void rendersRowCount() {
        assertEquals("# Rows: 42", renderer.rows(vars("rows", "42")));
    }

    @Test
    public void rendersPeriodFromTwoVariables() {
        Map<String, String> vars = vars("periodFrom", "2026-01-01");
        vars.put("periodTo", "2026-03-31");
        assertEquals("# Period: 2026-01-01 to 2026-03-31", renderer.period(vars));
    }

    @Test
    public void undefinedVariableFailsInsteadOfLeakingPlaceholder() {
        try {
            renderer.renderBanner("# ${notProvided}", new LinkedHashMap<String, String>());
            fail("expected an undefined banner variable to be rejected");
        } catch (IllegalArgumentException expected) {
            assertTrue("message should name the variable",
                    expected.getMessage().contains("notProvided"));
        }
    }

    @Test
    public void reportNameContainingATokenIsInsertedLiterally() {
        assertEquals("# OtterWorks Report: Q1 ${cost} review",
                renderer.title(vars("reportName", "Q1 ${cost} review")));
    }

    private Map<String, String> vars(String key, String value) {
        Map<String, String> vars = new LinkedHashMap<String, String>();
        vars.put(key, value);
        return vars;
    }
}
