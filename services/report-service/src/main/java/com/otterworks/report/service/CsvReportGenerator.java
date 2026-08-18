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
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Generates CSV reports using OpenCSV 4.6.
 *
 * LEGACY PATTERNS:
 * - OpenCSV 4.x (2018). Upgrade target: 5.9+ (different bean mapping API)
 * - FileWriter without explicit charset (platform-dependent encoding)
 * - Manual column extraction from Map keys
 *
 * UPGRADE NOTES:
 * - OpenCSV 5.x changed CSVWriter constructor signatures
 * - Bean-to-CSV mapping API completely rewritten in 5.x
 * - Should specify charset explicitly (StandardCharsets.UTF_8)
 */
@Component
public class CsvReportGenerator {

    private static final Logger logger = LoggerFactory.getLogger(CsvReportGenerator.class);

    private final ReportHeaderRenderer headerRenderer;

    public CsvReportGenerator() {
        this(new ReportHeaderRenderer());
    }

    public CsvReportGenerator(ReportHeaderRenderer headerRenderer) {
        this.headerRenderer = headerRenderer;
    }

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

                // Write metadata comments from the configured banner templates
                Map<String, String> bannerVars = new LinkedHashMap<String, String>();
                bannerVars.put("reportName", report.getReportName());
                bannerVars.put("generated", ReportDateUtils.toDisplayString(new Date()));
                bannerVars.put("periodFrom", ReportDateUtils.toDisplayString(report.getDateFrom()));
                bannerVars.put("periodTo", ReportDateUtils.toDisplayString(report.getDateTo()));
                bannerVars.put("rows", String.valueOf(data.size()));

                writer.writeNext(new String[]{headerRenderer.title(bannerVars)});
                writer.writeNext(new String[]{headerRenderer.generated(bannerVars)});
                writer.writeNext(new String[]{headerRenderer.period(bannerVars)});
                writer.writeNext(new String[]{headerRenderer.rows(bannerVars)});
                writer.writeNext(new String[]{headerRenderer.footer()});
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
