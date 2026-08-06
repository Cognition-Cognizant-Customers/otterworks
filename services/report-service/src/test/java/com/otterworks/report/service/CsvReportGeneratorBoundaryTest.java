package com.otterworks.report.service;

import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportStatus;
import com.otterworks.report.model.ReportType;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

/**
 * Result-set size and malformed-input boundaries for {@link CsvReportGenerator} (WP-12).
 *
 * Complements the existing happy-path suite with the zero / one / very-large row
 * counts, ragged rows, and values that need CSV quoting. Each test gets its own
 * output directory keyed by a UUID, so nothing is shared between tests.
 */
public class CsvReportGeneratorBoundaryTest {

    private static final int LARGE_ROW_COUNT = 5000;

    private CsvReportGenerator generator;
    private File outputDir;

    @Before
    public void setUp() {
        generator = new CsvReportGenerator();
        outputDir = new File(System.getProperty("java.io.tmpdir"), "csv-boundary-" + UUID.randomUUID());
        assertTrue(outputDir.mkdirs());
    }

    @After
    public void tearDown() {
        File[] files = outputDir.listFiles();
        if (files != null) {
            for (File f : files) {
                f.delete();
            }
        }
        outputDir.delete();
    }

    private Report report(String name) {
        Report r = new Report();
        r.setId(1L);
        r.setReportName(name);
        r.setCategory(ReportCategory.USAGE_ANALYTICS);
        r.setReportType(ReportType.CSV);
        r.setStatus(ReportStatus.GENERATING);
        r.setRequestedBy("user-001");
        r.setDateFrom(new Date(1709251200000L)); // 2024-03-01T00:00:00Z
        r.setDateTo(new Date(1709337600000L)); // 2024-03-02T00:00:00Z
        return r;
    }

    private List<Map<String, Object>> rows(int count) {
        List<Map<String, Object>> data = new ArrayList<Map<String, Object>>();
        for (int i = 0; i < count; i++) {
            Map<String, Object> row = new LinkedHashMap<String, Object>();
            row.put("event_id", "evt-" + i);
            row.put("user_id", "user-" + (i % 5));
            row.put("duration_ms", i);
            data.add(row);
        }
        return data;
    }

    private List<String> readLines(File file) throws IOException {
        List<String> lines = new ArrayList<String>();
        // The generator writes with FileWriter, i.e. the platform default charset,
        // so the reader has to match it to round-trip.
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(new FileInputStream(file), Charset.defaultCharset()));
        try {
            String line;
            while ((line = reader.readLine()) != null) {
                lines.add(line);
            }
        } finally {
            reader.close();
        }
        return lines;
    }

    // ---- result-set size boundaries: 0, 1, many ----

    @Test
    public void emptyResultSetProducesAZeroLengthFileWithNoHeaderAtAll() throws IOException {
        File file = generator.generateCsv(report("Empty Report"), rows(0), outputDir.getAbsolutePath());

        assertTrue(file.exists());
        assertEquals(0L, file.length());
        assertTrue(readLines(file).isEmpty());
    }

    @Test
    public void singleRowResultSetProducesFiveMetadataLinesAHeaderAndOneRow() throws IOException {
        File file = generator.generateCsv(report("Single Row"), rows(1), outputDir.getAbsolutePath());

        List<String> lines = readLines(file);
        // 4 comment lines + 1 blank + header + 1 data row
        assertEquals(7, lines.size());
        assertTrue(lines.get(0).contains("# OtterWorks Report: Single Row"));
        assertTrue(lines.get(3).contains("# Rows: 1"));
        assertTrue(lines.get(5).contains("event_id"));
        assertTrue(lines.get(6).contains("evt-0"));
    }

    @Test
    public void largeResultSetWritesEveryRow() throws IOException {
        File file = generator.generateCsv(report("Large Report"), rows(LARGE_ROW_COUNT), outputDir.getAbsolutePath());

        List<String> lines = readLines(file);
        assertEquals(LARGE_ROW_COUNT + 6, lines.size());
        assertTrue(lines.get(3).contains("# Rows: " + LARGE_ROW_COUNT));
        assertTrue(lines.get(lines.size() - 1).contains("evt-" + (LARGE_ROW_COUNT - 1)));
    }

    @Test
    public void rowCountBoundaryTrioAroundASingleRowIsConsistent() throws IOException {
        assertEquals(0, dataRowCount(0));
        assertEquals(1, dataRowCount(1));
        assertEquals(2, dataRowCount(2));
    }

    private int dataRowCount(int requested) throws IOException {
        File file = generator.generateCsv(report("Trio " + requested), rows(requested), outputDir.getAbsolutePath());
        List<String> lines = readLines(file);
        return lines.isEmpty() ? 0 : lines.size() - 6;
    }

    // ---- malformed / awkward input ----

    @Test
    public void nullCellsAreWrittenAsEmptyStrings() throws IOException {
        Map<String, Object> row = new LinkedHashMap<String, Object>();
        row.put("event_id", "evt-0");
        row.put("user_id", null);
        List<Map<String, Object>> data = new ArrayList<Map<String, Object>>();
        data.add(row);

        File file = generator.generateCsv(report("Nulls"), data, outputDir.getAbsolutePath());

        assertEquals("\"evt-0\",\"\"", readLines(file).get(6));
    }

    @Test
    public void raggedRowsAreProjectedOntoTheFirstRowsColumns() throws IOException {
        // The header is taken from row 0 only, so a later row's extra key is dropped
        // and a missing key becomes empty. Documented so a schema-union rewrite is visible.
        Map<String, Object> first = new LinkedHashMap<String, Object>();
        first.put("a", "1");
        first.put("b", "2");
        Map<String, Object> second = new LinkedHashMap<String, Object>();
        second.put("a", "3");
        second.put("c", "surprise");

        List<Map<String, Object>> data = new ArrayList<Map<String, Object>>();
        data.add(first);
        data.add(second);

        List<String> lines = readLines(generator.generateCsv(report("Ragged"), data, outputDir.getAbsolutePath()));

        assertEquals("\"a\",\"b\"", lines.get(5));
        assertEquals("\"3\",\"\"", lines.get(7));
        assertFalse(lines.get(7).contains("surprise"));
    }

    @Test
    public void separatorsQuotesAndNewlinesInsideValuesAreQuoted() throws IOException {
        Map<String, Object> row = new LinkedHashMap<String, Object>();
        row.put("comma", "a,b");
        row.put("quote", "say \"hi\"");
        row.put("newline", "line1\nline2");
        List<Map<String, Object>> data = new ArrayList<Map<String, Object>>();
        data.add(row);

        List<String> lines = readLines(generator.generateCsv(report("Quoting"), data, outputDir.getAbsolutePath()));

        assertEquals("\"a,b\",\"say \"\"hi\"\"\",\"line1", lines.get(6));
        assertEquals("line2\"", lines.get(7));
    }

    @Test
    public void nonAsciiValuesRoundTripThroughThePlatformCharset() throws IOException {
        // FINDING (WP-12): the generator uses FileWriter with no explicit charset,
        // so the encoding of a delivered CSV depends on the JVM's default. Asserted
        // against the default charset because that is the only self-consistent read.
        Map<String, Object> row = new LinkedHashMap<String, Object>();
        row.put("name", "Ottertjärn \u00e9\u00e8");
        List<Map<String, Object>> data = new ArrayList<Map<String, Object>>();
        data.add(row);

        List<String> lines = readLines(generator.generateCsv(report("Unicode"), data, outputDir.getAbsolutePath()));

        assertEquals("\"Ottertjärn \u00e9\u00e8\"", lines.get(6));
    }

    @Test
    public void aReportNameOfOnlySeparatorsSanitisesToUnderscores() throws IOException {
        File file = generator.generateCsv(report("../../etc/passwd"), rows(1), outputDir.getAbsolutePath());

        assertEquals(outputDir.getAbsolutePath(), file.getParentFile().getAbsolutePath());
        assertTrue(file.getName(), file.getName().startsWith("______etc_passwd_"));
        assertTrue(file.getName().endsWith(".csv"));
    }

    @Test
    public void aNestedOutputDirectoryIsCreatedOnDemand() throws IOException {
        File nested = new File(outputDir, "a/b/c");

        File file = generator.generateCsv(report("Nested"), rows(1), nested.getAbsolutePath());

        assertTrue(file.exists());
        assertNotNull(file.getParentFile());
        file.delete();
        nested.delete();
    }

    // ---- date-range boundaries reflected in the metadata block ----

    @Test
    public void nullReportDatesRenderAsNotAvailableInTheMetadataBlock() throws IOException {
        Report r = report("No Dates");
        r.setDateFrom(null);
        r.setDateTo(null);

        List<String> lines = readLines(generator.generateCsv(r, rows(1), outputDir.getAbsolutePath()));

        assertEquals("\"# Period: N/A to N/A\"", lines.get(2));
    }

    @Test
    public void aZeroWidthDateRangeRendersTheSameInstantTwice() throws IOException {
        Report r = report("Instant Range");
        Date sameInstant = new Date(1709251200000L);
        r.setDateFrom(sameInstant);
        r.setDateTo(sameInstant);

        List<String> lines = readLines(generator.generateCsv(r, rows(1), outputDir.getAbsolutePath()));

        assertEquals("\"# Period: Mar 01, 2024 00:00 to Mar 01, 2024 00:00\"", lines.get(2));
    }

    @Test
    public void anInvertedDateRangeIsWrittenWithoutComplaint() throws IOException {
        // Negative case: the generator does not validate dateFrom <= dateTo.
        Report r = report("Inverted Range");
        r.setDateFrom(new Date(1709337600000L)); // 2024-03-02
        r.setDateTo(new Date(1709251200000L)); // 2024-03-01

        List<String> lines = readLines(generator.generateCsv(r, rows(1), outputDir.getAbsolutePath()));

        assertEquals("\"# Period: Mar 02, 2024 00:00 to Mar 01, 2024 00:00\"", lines.get(2));
    }
}
