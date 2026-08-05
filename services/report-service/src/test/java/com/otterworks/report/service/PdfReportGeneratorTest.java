package com.otterworks.report.service;

import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportStatus;
import com.otterworks.report.model.ReportType;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Unit tests for {@link PdfReportGenerator}.
 *
 * Validates that the generator produces a syntactically valid PDF file with
 * correct size, header bytes, and non-trivial content. Does not require a
 * Spring context -- instantiates the generator directly.
 *
  */
public class PdfReportGeneratorTest {

    private PdfReportGenerator generator;
    private File outputDir;

    @BeforeEach
    public void setUp() {
        generator = new PdfReportGenerator();
        outputDir = new File(System.getProperty("java.io.tmpdir"), "pdf-test-" + System.currentTimeMillis());
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
    public void generatePdfProducesNonEmptyFile() throws IOException {
        Report report = buildReport("Usage Analytics Report");
        List<Map<String, Object>> data = buildSampleData(5);

        File pdf = generator.generatePdf(report, data, outputDir.getAbsolutePath());

        assertNotNull(pdf, "PDF file should not be null");
        assertTrue(pdf.exists(), "PDF file should exist");
        assertTrue(pdf.length() > 0, "PDF file should have content");
        assertTrue(pdf.getName().endsWith(".pdf"), "PDF file should have .pdf extension");
    }

    @Test
    public void generatedPdfStartsWithPdfHeader() throws IOException {
        Report report = buildReport("Header Check Report");
        List<Map<String, Object>> data = buildSampleData(3);

        File pdf = generator.generatePdf(report, data, outputDir.getAbsolutePath());

        byte[] header = new byte[5];
        try (FileInputStream fis = new FileInputStream(pdf)) {
            int bytesRead = fis.read(header);
            assertEquals(5, bytesRead, "Should read 5 header bytes");
        }
        // PDF files always start with %PDF-
        assertEquals('%', (char) header[0], "First byte should be '%'");
        assertEquals('P', (char) header[1], "Second byte should be 'P'");
        assertEquals('D', (char) header[2], "Third byte should be 'D'");
        assertEquals('F', (char) header[3], "Fourth byte should be 'F'");
        assertEquals('-', (char) header[4], "Fifth byte should be '-'");
    }

    @Test
    public void generatePdfWithMultipleRowsProducesLargerFile() throws IOException {
        Report report1 = buildReport("Small Data Report");
        List<Map<String, Object>> smallData = buildSampleData(2);
        File smallPdf = generator.generatePdf(report1, smallData, outputDir.getAbsolutePath());

        Report report2 = buildReport("Large Data Report");
        List<Map<String, Object>> largeData = buildSampleData(50);
        File largePdf = generator.generatePdf(report2, largeData, outputDir.getAbsolutePath());

        assertTrue(largePdf.length() > smallPdf.length(), "Larger dataset should produce a larger PDF");
    }

    @Test
    public void generatePdfWithEmptyDataProducesValidPdf() throws IOException {
        Report report = buildReport("Empty Data Report");
        List<Map<String, Object>> emptyData = new ArrayList<Map<String, Object>>();

        File pdf = generator.generatePdf(report, emptyData, outputDir.getAbsolutePath());

        assertNotNull(pdf, "PDF file should not be null");
        assertTrue(pdf.exists(), "PDF file should exist even with no data");
        assertTrue(pdf.length() > 0, "PDF file should have content (header/footer)");

        byte[] header = new byte[5];
        try (FileInputStream fis = new FileInputStream(pdf)) {
            fis.read(header);
        }
        assertEquals('%', (char) header[0]);
        assertEquals('P', (char) header[1]);
    }

    @Test
    public void generatePdfFileNameContainsReportName() throws IOException {
        Report report = buildReport("Quarterly Audit Summary");
        List<Map<String, Object>> data = buildSampleData(1);

        File pdf = generator.generatePdf(report, data, outputDir.getAbsolutePath());

        assertTrue(pdf.getName().startsWith("quarterly_audit_summary_"), "File name should contain sanitized report name");
    }

    // ---- Helpers ----

    private Report buildReport(String name) {
        Report report = new Report();
        report.setId(1L);
        report.setReportName(name);
        report.setCategory(ReportCategory.USAGE_ANALYTICS);
        report.setReportType(ReportType.PDF);
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
        String[] actions = {"login", "upload", "download", "share", "delete"};

        for (int i = 0; i < rows; i++) {
            Map<String, Object> row = new LinkedHashMap<String, Object>();
            row.put("user_id", users[i % users.length]);
            row.put("action", actions[i % actions.length]);
            row.put("timestamp", new Date(System.currentTimeMillis() - (long) i * 3600000));
            row.put("file_count", i * 3 + 1);
            row.put("bytes_transferred", (i + 1) * 1024L);
            data.add(row);
        }
        return data;
    }
}
