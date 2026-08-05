package com.otterworks.report.service;

import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportStatus;
import com.otterworks.report.model.ReportType;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Unit tests for {@link CsvReportGenerator}.
 *
 * Validates that the generator produces a well-formed CSV file with correct
 * column headers, metadata comment lines, and data rows. Does not require
 * a Spring context.
 *
  */
public class CsvReportGeneratorTest {

    private CsvReportGenerator generator;
    private File outputDir;

    @BeforeEach
    public void setUp() {
        generator = new CsvReportGenerator();
        outputDir = new File(System.getProperty("java.io.tmpdir"), "csv-test-" + System.currentTimeMillis());
        outputDir.mkdirs();
    }

    @AfterEach
    public void tearDown() {
        if (outputDir != null && outputDir.exists()) {
            File[] files = outputDir.listFiles();
            if (files != null) {
                for (File f : files) {
                    f.delete();
                }
            }
            outputDir.delete();
        }
    }

    @Test
    public void generateCsvProducesNonEmptyFile() throws IOException {
        Report report = buildReport("Audit Log Export");
        List<Map<String, Object>> data = buildSampleData(5);

        File csv = generator.generateCsv(report, data, outputDir.getAbsolutePath());

        assertNotNull(csv, "CSV file should not be null");
        assertTrue(csv.exists(), "CSV file should exist");
        assertTrue(csv.length() > 0, "CSV file should have content");
        assertTrue(csv.getName().endsWith(".csv"), "CSV file should have .csv extension");
    }

    @Test
    public void generatedCsvContainsMetadataComments() throws IOException {
        Report report = buildReport("Metadata Check Report");
        List<Map<String, Object>> data = buildSampleData(3);

        File csv = generator.generateCsv(report, data, outputDir.getAbsolutePath());
        List<String> lines = readAllLines(csv);

        assertTrue(lines.size() > 0, "CSV should have metadata lines");

        boolean hasReportNameComment = false;
        boolean hasGeneratedComment = false;
        boolean hasPeriodComment = false;
        boolean hasRowsComment = false;

        for (String line : lines) {
            if (line.contains("OtterWorks Report") && line.contains("Metadata Check Report")) {
                hasReportNameComment = true;
            }
            if (line.contains("Generated:")) {
                hasGeneratedComment = true;
            }
            if (line.contains("Period:")) {
                hasPeriodComment = true;
            }
            if (line.contains("Rows:")) {
                hasRowsComment = true;
            }
        }

        assertTrue(hasReportNameComment, "CSV should contain report name comment");
        assertTrue(hasGeneratedComment, "CSV should contain generated timestamp comment");
        assertTrue(hasPeriodComment, "CSV should contain period comment");
        assertTrue(hasRowsComment, "CSV should contain row count comment");
    }

    @Test
    public void generatedCsvContainsCorrectColumnHeaders() throws IOException {
        Report report = buildReport("Column Header Report");
        List<Map<String, Object>> data = buildSampleData(2);

        File csv = generator.generateCsv(report, data, outputDir.getAbsolutePath());
        List<String> lines = readAllLines(csv);

        // Find the first non-comment, non-empty line (the header row)
        String headerLine = null;
        for (String line : lines) {
            if (!line.startsWith("\"#") && !line.trim().isEmpty() && !line.equals("\"\"")) {
                headerLine = line;
                break;
            }
        }

        assertNotNull(headerLine, "Should find a header line");
        assertTrue(headerLine.contains("user_id"), "Header should contain user_id column");
        assertTrue(headerLine.contains("action"), "Header should contain action column");
        assertTrue(headerLine.contains("timestamp"), "Header should contain timestamp column");
        assertTrue(headerLine.contains("department"), "Header should contain department column");
    }

    @Test
    public void generatedCsvContainsCorrectNumberOfDataRows() throws IOException {
        int expectedRows = 10;
        Report report = buildReport("Row Count Report");
        List<Map<String, Object>> data = buildSampleData(expectedRows);

        File csv = generator.generateCsv(report, data, outputDir.getAbsolutePath());
        List<String> lines = readAllLines(csv);

        // Count non-comment, non-empty, non-header data lines
        int dataLineCount = 0;
        boolean headerSeen = false;
        for (String line : lines) {
            if (line.startsWith("\"#") || line.trim().isEmpty() || line.equals("\"\"")) {
                continue;
            }
            if (!headerSeen) {
                headerSeen = true;
                continue;
            }
            dataLineCount++;
        }

        assertEquals(expectedRows, dataLineCount, "Data row count should match input");
    }

    @Test
    public void generateCsvWithEmptyDataProducesEmptyFile() throws IOException {
        Report report = buildReport("Empty CSV Report");
        List<Map<String, Object>> emptyData = new ArrayList<Map<String, Object>>();

        File csv = generator.generateCsv(report, emptyData, outputDir.getAbsolutePath());

        assertNotNull(csv, "CSV file should not be null");
        assertTrue(csv.exists(), "CSV file should exist even with no data");
    }

    @Test
    public void generatedCsvFileNameContainsReportName() throws IOException {
        Report report = buildReport("Monthly Security Audit");
        List<Map<String, Object>> data = buildSampleData(1);

        File csv = generator.generateCsv(report, data, outputDir.getAbsolutePath());

        assertTrue(csv.getName().startsWith("monthly_security_audit_"), "File name should contain sanitized report name");
    }

    // ---- Helpers ----

    private Report buildReport(String name) {
        Report report = new Report();
        report.setId(1L);
        report.setReportName(name);
        report.setCategory(ReportCategory.AUDIT_LOG);
        report.setReportType(ReportType.CSV);
        report.setStatus(ReportStatus.GENERATING);
        report.setRequestedBy("test-user");
        report.setDateFrom(new Date(System.currentTimeMillis() - 86400000L * 30));
        report.setDateTo(new Date());
        report.setCreatedAt(new Date());
        return report;
    }

    private List<Map<String, Object>> buildSampleData(int rows) {
        List<Map<String, Object>> data = new ArrayList<Map<String, Object>>();
        String[] users = {"alice", "bob", "carol", "dave", "eve"};
        String[] actions = {"LOGIN", "FILE_UPLOAD", "FILE_DELETE", "SHARE", "PERMISSION_CHANGE"};
        String[] departments = {"Engineering", "Marketing", "Finance", "Legal", "Operations"};

        for (int i = 0; i < rows; i++) {
            Map<String, Object> row = new LinkedHashMap<String, Object>();
            row.put("user_id", users[i % users.length]);
            row.put("action", actions[i % actions.length]);
            row.put("timestamp", new Date(System.currentTimeMillis() - (long) i * 3600000));
            row.put("department", departments[i % departments.length]);
            row.put("ip_address", "192.168.1." + (i + 1));
            data.add(row);
        }
        return data;
    }

    private List<String> readAllLines(File file) throws IOException {
        List<String> lines = new ArrayList<String>();
        try (BufferedReader reader = new BufferedReader(new FileReader(file))) {
            String line;
            while ((line = reader.readLine()) != null) {
                lines.add(line);
            }
        }
        return lines;
    }
}
