package com.otterworks.report.service;

import java.awt.Color;
import com.lowagie.text.Chunk;
import com.lowagie.text.Document;
import com.lowagie.text.DocumentException;
import com.lowagie.text.Element;
import com.lowagie.text.Font;
import com.lowagie.text.FontFactory;
import com.lowagie.text.PageSize;
import com.lowagie.text.Paragraph;
import com.lowagie.text.Phrase;
import com.lowagie.text.pdf.PdfPCell;
import com.lowagie.text.pdf.PdfPTable;
import com.lowagie.text.pdf.PdfWriter;
import com.otterworks.report.model.Report;
import com.otterworks.report.util.ReportDateUtils;
import org.apache.commons.lang3.StringUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.LinkedHashSet;

/**
 * Generates PDF reports using OpenPDF (LGPL).
 */
@Component
public class PdfReportGenerator {

    private static final Logger logger = LoggerFactory.getLogger(PdfReportGenerator.class);

    private static final Font TITLE_FONT = FontFactory.getFont(FontFactory.HELVETICA_BOLD, 18, Color.DARK_GRAY);
    private static final Font SUBTITLE_FONT = FontFactory.getFont(FontFactory.HELVETICA, 12, Color.GRAY);
    private static final Font HEADER_FONT = FontFactory.getFont(FontFactory.HELVETICA_BOLD, 10, Color.WHITE);
    private static final Font CELL_FONT = FontFactory.getFont(FontFactory.HELVETICA, 9, Color.BLACK);
    private static final Color HEADER_BG = new Color(44, 62, 80);
    private static final Color ALT_ROW_BG = new Color(236, 240, 241);

    /**
     * Generate a PDF report file.
     *
     * @return the File object for the generated PDF
     */
    public File generatePdf(Report report, List<Map<String, Object>> data, String outputDir) throws IOException {
        String fileName = buildFileName(report, "pdf");
        File outputFile = new File(outputDir, fileName);
        outputFile.getParentFile().mkdirs();

        Document document = new Document(PageSize.A4.rotate(), 36, 36, 54, 36);

        try {
            PdfWriter.getInstance(document, new FileOutputStream(outputFile));
            document.open();

            // Title
            Paragraph title = new Paragraph("OtterWorks Report", TITLE_FONT);
            title.setAlignment(Element.ALIGN_CENTER);
            title.setSpacingAfter(8);
            document.add(title);

            // Report name and metadata
            Paragraph subtitle = new Paragraph(report.getReportName(), SUBTITLE_FONT);
            subtitle.setAlignment(Element.ALIGN_CENTER);
            subtitle.setSpacingAfter(4);
            document.add(subtitle);

            String dateRange = "Period: "
                    + ReportDateUtils.toDisplayString(report.getDateFrom())
                    + " — "
                    + ReportDateUtils.toDisplayString(report.getDateTo());
            Paragraph dateInfo = new Paragraph(dateRange, SUBTITLE_FONT);
            dateInfo.setAlignment(Element.ALIGN_CENTER);
            dateInfo.setSpacingAfter(4);
            document.add(dateInfo);

            Paragraph generated = new Paragraph(
                    "Generated: " + ReportDateUtils.toDisplayString(new Date())
                    + " | Rows: " + data.size(),
                    SUBTITLE_FONT);
            generated.setAlignment(Element.ALIGN_CENTER);
            generated.setSpacingAfter(20);
            document.add(generated);

            // Data table
            if (!data.isEmpty()) {
                Set<String> columns = new LinkedHashSet<>(data.get(0).keySet());
                PdfPTable table = new PdfPTable(columns.size());
                table.setWidthPercentage(100);

                // Header row
                for (String col : columns) {
                    PdfPCell headerCell = new PdfPCell(new Phrase(formatColumnName(col), HEADER_FONT));
                    headerCell.setBackgroundColor(HEADER_BG);
                    headerCell.setHorizontalAlignment(Element.ALIGN_CENTER);
                    headerCell.setPadding(6);
                    table.addCell(headerCell);
                }

                // Data rows
                int rowIndex = 0;
                for (Map<String, Object> row : data) {
                    for (String col : columns) {
                        Object value = row.get(col);
                        String cellValue = value != null ? value.toString() : "";
                        PdfPCell cell = new PdfPCell(new Phrase(cellValue, CELL_FONT));
                        cell.setPadding(4);
                        if (rowIndex % 2 == 1) {
                            cell.setBackgroundColor(ALT_ROW_BG);
                        }
                        table.addCell(cell);
                    }
                    rowIndex++;
                }

                document.add(table);
            } else {
                document.add(new Paragraph("No data available for the selected criteria.", SUBTITLE_FONT));
            }

            // Footer
            document.add(Chunk.NEWLINE);
            Paragraph footer = new Paragraph(
                    "This report was generated by OtterWorks Report Service v0.1.0. "
                    + "Data is subject to the reporting period specified above.",
                    new Font(Font.HELVETICA, 8, Font.ITALIC, Color.GRAY));
            footer.setAlignment(Element.ALIGN_CENTER);
            document.add(footer);

        } catch (DocumentException e) {
            logger.error("PDF generation failed for report {}: {}", report.getId(), e.getMessage());
            throw new IOException("Failed to generate PDF: " + e.getMessage(), e);
        } finally {
            document.close();
        }

        logger.info("Generated PDF report: {} ({} bytes)", outputFile.getAbsolutePath(), outputFile.length());
        return outputFile;
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
