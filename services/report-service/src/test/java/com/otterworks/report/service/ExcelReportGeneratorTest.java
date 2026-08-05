package com.otterworks.report.service;

import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportStatus;
import com.otterworks.report.model.ReportType;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.File;
import java.io.FileInputStream;
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
 * Unit tests for {@link ExcelReportGenerator}.
 *
 * Uses Apache POI to read back the generated .xlsx file and verify its
 * structure: sheet names, summary metadata, column headers, and data rows.
 * Does not require a Spring context.
 *
  */
public class ExcelReportGeneratorTest {

    private ExcelReportGenerator generator;
    private File outputDir;

    @BeforeEach
    public void setUp() {
        generator = new ExcelReportGenerator();
        outputDir = new File(System.getProperty("java.io.tmpdir"), "excel-test-" + System.currentTimeMillis());
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
    public void generateExcelProducesNonEmptyFile() throws IOException {
        Report report = buildReport("Storage Summary Report");
        List<Map<String, Object>> data = buildSampleData(5);

        File xlsx = generator.generateExcel(report, data, outputDir.getAbsolutePath());

        assertNotNull(xlsx, "Excel file should not be null");
        assertTrue(xlsx.exists(), "Excel file should exist");
        assertTrue(xlsx.length() > 0, "Excel file should have content");
        assertTrue(xlsx.getName().endsWith(".xlsx"), "Excel file should have .xlsx extension");
    }

    @Test
    public void generatedExcelIsReadableByPoi() throws IOException {
        Report report = buildReport("Readable Excel Report");
        List<Map<String, Object>> data = buildSampleData(3);

        File xlsx = generator.generateExcel(report, data, outputDir.getAbsolutePath());

        try (FileInputStream fis = new FileInputStream(xlsx);
             Workbook workbook = new XSSFWorkbook(fis)) {
            assertNotNull(workbook, "Workbook should not be null");
            assertTrue(workbook.getNumberOfSheets() > 0, "Workbook should have at least one sheet");
        }
    }

    @Test
    public void generatedExcelHasSummaryAndDataSheets() throws IOException {
        Report report = buildReport("Two Sheet Report");
        List<Map<String, Object>> data = buildSampleData(5);

        File xlsx = generator.generateExcel(report, data, outputDir.getAbsolutePath());

        try (FileInputStream fis = new FileInputStream(xlsx);
             Workbook workbook = new XSSFWorkbook(fis)) {
            assertEquals(2, workbook.getNumberOfSheets(), "Workbook should have 2 sheets");
            assertEquals("Summary", workbook.getSheetName(0), "First sheet should be 'Summary'");
            assertEquals("Data", workbook.getSheetName(1), "Second sheet should be 'Data'");
        }
    }

    @Test
    public void summarySheetContainsReportMetadata() throws IOException {
        Report report = buildReport("Metadata Verification Report");
        List<Map<String, Object>> data = buildSampleData(7);

        File xlsx = generator.generateExcel(report, data, outputDir.getAbsolutePath());

        try (FileInputStream fis = new FileInputStream(xlsx);
             Workbook workbook = new XSSFWorkbook(fis)) {
            Sheet summary = workbook.getSheet("Summary");
            assertNotNull(summary, "Summary sheet should exist");

            // Row 0: Title "OtterWorks Report"
            Row titleRow = summary.getRow(0);
            assertNotNull(titleRow, "Title row should exist");
            assertEquals("OtterWorks Report", titleRow.getCell(0).getStringCellValue(), "Title should be 'OtterWorks Report'");

            // Row 2: Report Name label and value
            Row nameRow = summary.getRow(2);
            assertNotNull(nameRow, "Name row should exist");
            assertEquals("Report Name:", nameRow.getCell(0).getStringCellValue(), "Name label");
            assertEquals("Metadata Verification Report", nameRow.getCell(1).getStringCellValue(), "Name value");

            // Row 3: Category
            Row catRow = summary.getRow(3);
            assertNotNull(catRow, "Category row should exist");
            assertEquals("Category:", catRow.getCell(0).getStringCellValue(), "Category label");
            assertEquals("STORAGE_SUMMARY", catRow.getCell(1).getStringCellValue(), "Category value");

            // Row 6: Total Rows
            Row countRow = summary.getRow(6);
            assertNotNull(countRow, "Count row should exist");
            assertEquals("Total Rows:", countRow.getCell(0).getStringCellValue(), "Count label");
            assertEquals(7.0, countRow.getCell(1).getNumericCellValue(), 0.001, "Row count should match data size");
        }
    }

    @Test
    public void dataSheetContainsCorrectHeaders() throws IOException {
        Report report = buildReport("Header Check Report");
        List<Map<String, Object>> data = buildSampleData(2);

        File xlsx = generator.generateExcel(report, data, outputDir.getAbsolutePath());

        try (FileInputStream fis = new FileInputStream(xlsx);
             Workbook workbook = new XSSFWorkbook(fis)) {
            Sheet dataSheet = workbook.getSheet("Data");
            assertNotNull(dataSheet, "Data sheet should exist");

            Row headerRow = dataSheet.getRow(0);
            assertNotNull(headerRow, "Header row should exist");

            // Column names are formatted by ExcelReportGenerator.formatColumnName
            // which replaces underscores with spaces and capitalizes
            List<String> expectedHeaders = new ArrayList<String>();
            expectedHeaders.add("File id");
            expectedHeaders.add("File name");
            expectedHeaders.add("Size bytes");
            expectedHeaders.add("Owner");
            expectedHeaders.add("Created at");

            for (int i = 0; i < expectedHeaders.size(); i++) {
                assertEquals(expectedHeaders.get(i), headerRow.getCell(i).getStringCellValue(), "Column " + i + " header");
            }
        }
    }

    @Test
    public void dataSheetContainsCorrectNumberOfRows() throws IOException {
        int expectedRows = 12;
        Report report = buildReport("Row Count Report");
        List<Map<String, Object>> data = buildSampleData(expectedRows);

        File xlsx = generator.generateExcel(report, data, outputDir.getAbsolutePath());

        try (FileInputStream fis = new FileInputStream(xlsx);
             Workbook workbook = new XSSFWorkbook(fis)) {
            Sheet dataSheet = workbook.getSheet("Data");
            assertNotNull(dataSheet, "Data sheet should exist");

            // Physical rows = 1 header + N data rows
            assertEquals(expectedRows + 1, dataSheet.getPhysicalNumberOfRows(), "Data sheet should have header + data rows");
        }
    }

    @Test
    public void dataSheetCellsContainExpectedValues() throws IOException {
        Report report = buildReport("Cell Value Report");
        List<Map<String, Object>> data = buildSampleData(3);

        File xlsx = generator.generateExcel(report, data, outputDir.getAbsolutePath());

        try (FileInputStream fis = new FileInputStream(xlsx);
             Workbook workbook = new XSSFWorkbook(fis)) {
            Sheet dataSheet = workbook.getSheet("Data");

            // Row 1 (first data row) should have the first record's values
            Row firstDataRow = dataSheet.getRow(1);
            assertNotNull(firstDataRow, "First data row should exist");

            // file_id column (index 0) should be "file-0"
            assertEquals("file-0", firstDataRow.getCell(0).getStringCellValue(), "First data row, file_id");
            // file_name column (index 1) should be "document_0.pdf"
            assertEquals("document_0.pdf", firstDataRow.getCell(1).getStringCellValue(), "First data row, file_name");
        }
    }

    @Test
    public void generateExcelWithEmptyDataProducesValidFile() throws IOException {
        Report report = buildReport("Empty Excel Report");
        List<Map<String, Object>> emptyData = new ArrayList<Map<String, Object>>();

        File xlsx = generator.generateExcel(report, emptyData, outputDir.getAbsolutePath());

        assertNotNull(xlsx, "Excel file should not be null");
        assertTrue(xlsx.exists(), "Excel file should exist");

        try (FileInputStream fis = new FileInputStream(xlsx);
             Workbook workbook = new XSSFWorkbook(fis)) {
            assertNotNull(workbook, "Workbook should be readable");
            Sheet summary = workbook.getSheet("Summary");
            assertNotNull(summary, "Summary sheet should exist even with no data");
        }
    }

    @Test
    public void generatedExcelFileNameContainsReportName() throws IOException {
        Report report = buildReport("Weekly File Usage Stats");
        List<Map<String, Object>> data = buildSampleData(1);

        File xlsx = generator.generateExcel(report, data, outputDir.getAbsolutePath());

        assertTrue(xlsx.getName().startsWith("weekly_file_usage_stats_"), "File name should contain sanitized report name");
    }

    // ---- Helpers ----

    private Report buildReport(String name) {
        Report report = new Report();
        report.setId(1L);
        report.setReportName(name);
        report.setCategory(ReportCategory.STORAGE_SUMMARY);
        report.setReportType(ReportType.EXCEL);
        report.setStatus(ReportStatus.GENERATING);
        report.setRequestedBy("test-user");
        report.setDateFrom(new Date(System.currentTimeMillis() - 86400000L * 30));
        report.setDateTo(new Date());
        report.setCreatedAt(new Date());
        return report;
    }

    private List<Map<String, Object>> buildSampleData(int rows) {
        List<Map<String, Object>> data = new ArrayList<Map<String, Object>>();
        String[] extensions = {"pdf", "docx", "xlsx", "png", "txt"};
        String[] owners = {"alice", "bob", "carol", "dave", "eve"};

        for (int i = 0; i < rows; i++) {
            Map<String, Object> row = new LinkedHashMap<String, Object>();
            row.put("file_id", "file-" + i);
            row.put("file_name", "document_" + i + "." + extensions[i % extensions.length]);
            row.put("size_bytes", (i + 1) * 2048L);
            row.put("owner", owners[i % owners.length]);
            row.put("created_at", new Date(System.currentTimeMillis() - (long) i * 86400000));
            data.add(row);
        }
        return data;
    }
}
