package com.otterworks.report.model;

import io.swagger.v3.oas.annotations.media.Schema;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.util.Date;
import java.util.Map;

/**
 * Request DTO for creating a new report.
 *
 * LEGACY PATTERNS:
 * - java.util.Date (target: java.time.Instant)
 * - Mutable POJO with setters (target: Java 16+ record)
 */
@Schema(description = "Request to generate a new report")
public class ReportRequest {

    @NotBlank(message = "Report name is required")
    @Schema(description = "Human-readable name for the report", requiredMode = Schema.RequiredMode.REQUIRED, example = "Monthly Usage Report")
    private String reportName;

    @NotNull(message = "Report category is required")
    @Schema(description = "Category of data to include", requiredMode = Schema.RequiredMode.REQUIRED)
    private ReportCategory category;

    @NotNull(message = "Report type is required")
    @Schema(description = "Output format", requiredMode = Schema.RequiredMode.REQUIRED, example = "PDF")
    private ReportType reportType;

    @NotBlank(message = "Requester ID is required")
    @Schema(description = "User ID requesting the report", requiredMode = Schema.RequiredMode.REQUIRED)
    private String requestedBy;

    // LEGACY: java.util.Date
    @Schema(description = "Start of reporting period")
    private Date dateFrom;

    // LEGACY: java.util.Date
    @Schema(description = "End of reporting period")
    private Date dateTo;

    @Schema(description = "Additional parameters for report generation")
    private Map<String, String> parameters;

    public ReportRequest() {
    }

    public String getReportName() {
        return reportName;
    }

    public void setReportName(String reportName) {
        this.reportName = reportName;
    }

    public ReportCategory getCategory() {
        return category;
    }

    public void setCategory(ReportCategory category) {
        this.category = category;
    }

    public ReportType getReportType() {
        return reportType;
    }

    public void setReportType(ReportType reportType) {
        this.reportType = reportType;
    }

    public String getRequestedBy() {
        return requestedBy;
    }

    public void setRequestedBy(String requestedBy) {
        this.requestedBy = requestedBy;
    }

    public Date getDateFrom() {
        return dateFrom;
    }

    public void setDateFrom(Date dateFrom) {
        this.dateFrom = dateFrom;
    }

    public Date getDateTo() {
        return dateTo;
    }

    public void setDateTo(Date dateTo) {
        this.dateTo = dateTo;
    }

    public Map<String, String> getParameters() {
        return parameters;
    }

    public void setParameters(Map<String, String> parameters) {
        this.parameters = parameters;
    }
}
