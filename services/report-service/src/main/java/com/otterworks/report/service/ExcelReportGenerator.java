package com.otterworks.report.service;

import com.otterworks.report.model.Report;
import com.otterworks.report.util.ReportDateUtils;
import org.apache.commons.lang3.StringUtils;
import org.apache.poi.ss.usermodel.BorderStyle;
import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.FillPatternType;
import org.apache.poi.ss.usermodel.Font;
import org.apache.poi.ss.usermodel.HorizontalAlignment;
import org.apache.poi.ss.usermodel.IndexedColors;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.util.CellRangeAddress;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Generates Excel (.xlsx) reports using Apache POI.
 *
 * LEGACY PATTERNS:
 * - Manual cell styling (verbose, repetitive)
 * - FileOutputStream for writing (should use OutputStream abstraction)
 *
 */
@Component
public class ExcelReportGenerator {

    private static final Logger logger = LoggerFactory.getLogger(ExcelReportGenerator.class);

    /**
     * Generate an Excel (.xlsx) report file.
     *
     * @return the File object for the generated Excel file
     */
    public File generateExcel(Report report, List<Map<String, Object>> data, String outputDir) throws IOException {
        String fileName = buildFileName(report, "xlsx");
        File outputFile = new File(outputDir, fileName);
        outputFile.getParentFile().mkdirs();

        // LEGACY: XSSFWorkbook loads entire workbook in memory.
        // For large reports, should use SXSSFWorkbook (streaming).
        Workbook workbook = new XSSFWorkbook();

        try {
            // Summary sheet
            Sheet summarySheet = workbook.createSheet("Summary");
            createSummarySheet(workbook, summarySheet, report, data.size());

            // Data sheet
            Sheet dataSheet = workbook.createSheet("Data");
            if (!data.isEmpty()) {
                createDataSheet(workbook, dataSheet, data);
            }

            // Auto-size columns on summary sheet
            for (int i = 0; i < 2; i++) {
                summarySheet.autoSizeColumn(i);
            }

            // Write to file
            try (FileOutputStream fos = new FileOutputStream(outputFile)) {
                workbook.write(fos);
            }
        } finally {
            workbook.close();
        }

        logger.info("Generated Excel report: {} ({} bytes)", outputFile.getAbsolutePath(), outputFile.length());
        return outputFile;
    }

    private void createSummarySheet(Workbook workbook, Sheet sheet, Report report, int rowCount) {
        CellStyle titleStyle = createTitleStyle(workbook);
        CellStyle labelStyle = createLabelStyle(workbook);
        CellStyle valueStyle = createValueStyle(workbook);

        // Title
        Row titleRow = sheet.createRow(0);
        titleRow.createCell(0).setCellValue("OtterWorks Report");
        titleRow.getCell(0).setCellStyle(titleStyle);
        sheet.addMergedRegion(new CellRangeAddress(0, 0, 0, 1));

        // Report name
        Row nameRow = sheet.createRow(2);
        nameRow.createCell(0).setCellValue("Report Name:");
        nameRow.getCell(0).setCellStyle(labelStyle);
        nameRow.createCell(1).setCellValue(report.getReportName());
        nameRow.getCell(1).setCellStyle(valueStyle);

        // Category
        Row catRow = sheet.createRow(3);
        catRow.createCell(0).setCellValue("Category:");
        catRow.getCell(0).setCellStyle(labelStyle);
        catRow.createCell(1).setCellValue(report.getCategory() != null ? report.getCategory().name() : "N/A");
        catRow.getCell(1).setCellStyle(valueStyle);

        // Date range
        Row dateRow = sheet.createRow(4);
        dateRow.createCell(0).setCellValue("Period:");
        dateRow.getCell(0).setCellStyle(labelStyle);
        dateRow.createCell(1).setCellValue(
                ReportDateUtils.toDisplayString(report.getDateFrom())
                + " to " + ReportDateUtils.toDisplayString(report.getDateTo()));
        dateRow.getCell(1).setCellStyle(valueStyle);

        // Generated at
        Row genRow = sheet.createRow(5);
        genRow.createCell(0).setCellValue("Generated:");
        genRow.getCell(0).setCellStyle(labelStyle);
        genRow.createCell(1).setCellValue(ReportDateUtils.toDisplayString(new Date()));
        genRow.getCell(1).setCellStyle(valueStyle);

        // Row count
        Row countRow = sheet.createRow(6);
        countRow.createCell(0).setCellValue("Total Rows:");
        countRow.getCell(0).setCellStyle(labelStyle);
        countRow.createCell(1).setCellValue(rowCount);
        countRow.getCell(1).setCellStyle(valueStyle);

        // Requested by
        Row reqRow = sheet.createRow(7);
        reqRow.createCell(0).setCellValue("Requested By:");
        reqRow.getCell(0).setCellStyle(labelStyle);
        reqRow.createCell(1).setCellValue(report.getRequestedBy());
        reqRow.getCell(1).setCellStyle(valueStyle);
    }

    private void createDataSheet(Workbook workbook, Sheet sheet, List<Map<String, Object>> data) {
        CellStyle headerStyle = createHeaderStyle(workbook);
        CellStyle cellStyle = createCellStyle(workbook);
        CellStyle altCellStyle = createAltCellStyle(workbook);

        Set<String> columns = new LinkedHashSet<>(data.get(0).keySet());

        // Header row
        Row headerRow = sheet.createRow(0);
        int colIdx = 0;
        for (String col : columns) {
            headerRow.createCell(colIdx).setCellValue(formatColumnName(col));
            headerRow.getCell(colIdx).setCellStyle(headerStyle);
            colIdx++;
        }

        // Data rows
        int rowIdx = 1;
        for (Map<String, Object> rowData : data) {
            Row row = sheet.createRow(rowIdx);
            colIdx = 0;
            CellStyle style = (rowIdx % 2 == 0) ? altCellStyle : cellStyle;
            for (String col : columns) {
                Object value = rowData.get(col);
                String cellValue = value != null ? value.toString() : "";

                row.createCell(colIdx).setCellValue(cellValue);
                row.getCell(colIdx).setCellStyle(style);
                colIdx++;
            }
            rowIdx++;
        }

        // Auto-size columns
        for (int i = 0; i < columns.size(); i++) {
            sheet.autoSizeColumn(i);
        }

        // Add auto-filter
        sheet.setAutoFilter(new CellRangeAddress(0, rowIdx - 1, 0, columns.size() - 1));
    }

    // ----- Style helpers -----

    private CellStyle createTitleStyle(Workbook workbook) {
        CellStyle style = workbook.createCellStyle();
        Font font = workbook.createFont();
        font.setBold(true);
        font.setFontHeightInPoints((short) 16);
        font.setColor(IndexedColors.DARK_BLUE.getIndex());
        style.setFont(font);
        return style;
    }

    private CellStyle createLabelStyle(Workbook workbook) {
        CellStyle style = workbook.createCellStyle();
        Font font = workbook.createFont();
        font.setBold(true);
        font.setFontHeightInPoints((short) 10);
        style.setFont(font);
        style.setAlignment(HorizontalAlignment.RIGHT);
        return style;
    }

    private CellStyle createValueStyle(Workbook workbook) {
        CellStyle style = workbook.createCellStyle();
        Font font = workbook.createFont();
        font.setFontHeightInPoints((short) 10);
        style.setFont(font);
        return style;
    }

    private CellStyle createHeaderStyle(Workbook workbook) {
        CellStyle style = workbook.createCellStyle();
        Font font = workbook.createFont();
        font.setBold(true);
        font.setColor(IndexedColors.WHITE.getIndex());
        font.setFontHeightInPoints((short) 10);
        style.setFont(font);
        style.setFillForegroundColor(IndexedColors.DARK_BLUE.getIndex());
        style.setFillPattern(FillPatternType.SOLID_FOREGROUND);
        style.setBorderBottom(BorderStyle.THIN);
        style.setAlignment(HorizontalAlignment.CENTER);
        return style;
    }

    private CellStyle createCellStyle(Workbook workbook) {
        CellStyle style = workbook.createCellStyle();
        style.setBorderBottom(BorderStyle.THIN);
        style.setBorderTop(BorderStyle.THIN);
        style.setBorderLeft(BorderStyle.THIN);
        style.setBorderRight(BorderStyle.THIN);
        return style;
    }

    private CellStyle createAltCellStyle(Workbook workbook) {
        CellStyle style = createCellStyle(workbook);
        style.setFillForegroundColor(IndexedColors.GREY_25_PERCENT.getIndex());
        style.setFillPattern(FillPatternType.SOLID_FOREGROUND);
        return style;
    }

    private String formatColumnName(String columnName) {
        if (StringUtils.isBlank(columnName)) {
            return "";
        }
        return StringUtils.capitalize(columnName.replace("_", " "));
    }

    private String buildFileName(Report report, String extension) {
        String safeName = report.getReportName().replaceAll("[^a-zA-Z0-9]", "_").toLowerCase();
        return safeName + "_" + ReportDateUtils.toFileNameString(new Date()) + "." + extension;
    }
}
