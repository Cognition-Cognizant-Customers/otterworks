package com.otterworks.report.service;

import com.opencsv.CSVWriter;
import com.otterworks.report.model.Report;
import com.otterworks.report.util.ReportDateUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Generates CSV reports using OpenCSV.
 *
 * LEGACY PATTERNS:
 * - FileWriter without explicit charset (platform-dependent encoding)
 * - Manual column extraction from Map keys
 *
 */
@Component
public class CsvReportGenerator {

    private static final Logger logger = LoggerFactory.getLogger(CsvReportGenerator.class);

    /**
     * Generate a CSV report file.
     *
     * @return the File object for the generated CSV
     */
    public File generateCsv(Report report, List<Map<String, Object>> data, String outputDir) throws IOException {
        String fileName = buildFileName(report, "csv");
        File outputFile = new File(outputDir, fileName);
        outputFile.getParentFile().mkdirs();

        // LEGACY: FileWriter without charset (uses platform default encoding)
        // Modern: new OutputStreamWriter(new FileOutputStream(file), StandardCharsets.UTF_8)
        try (CSVWriter writer = new CSVWriter(new FileWriter(outputFile))) {

            if (!data.isEmpty()) {
                // Extract column headers from first row
                Set<String> columns = new LinkedHashSet<>(data.get(0).keySet());
                String[] header = columns.toArray(new String[0]);

                // Write metadata comments
                writer.writeNext(new String[]{"# OtterWorks Report: " + report.getReportName()});
                writer.writeNext(new String[]{"# Generated: " + ReportDateUtils.toDisplayString(new Date())});
                writer.writeNext(new String[]{"# Period: "
                        + ReportDateUtils.toDisplayString(report.getDateFrom())
                        + " to " + ReportDateUtils.toDisplayString(report.getDateTo())});
                writer.writeNext(new String[]{"# Rows: " + data.size()});
                writer.writeNext(new String[]{""});

                // Write header row
                writer.writeNext(header);

                // Write data rows
                for (Map<String, Object> row : data) {
                    String[] values = new String[columns.size()];
                    int i = 0;
                    for (String col : columns) {
                        Object value = row.get(col);
                        values[i++] = value != null ? value.toString() : "";
                    }
                    writer.writeNext(values);
                }
            }
        }

        logger.info("Generated CSV report: {} ({} bytes)", outputFile.getAbsolutePath(), outputFile.length());
        return outputFile;
    }

    private String buildFileName(Report report, String extension) {
        String safeName = report.getReportName().replaceAll("[^a-zA-Z0-9]", "_").toLowerCase();
        return safeName + "_" + ReportDateUtils.toFileNameString(new Date()) + "." + extension;
    }
}
