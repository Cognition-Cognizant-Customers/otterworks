package com.otterworks.report.model;

import io.swagger.annotations.ApiModel;
import io.swagger.annotations.ApiModelProperty;

import javax.validation.constraints.NotBlank;
import javax.validation.constraints.NotNull;
import java.util.Date;
import java.util.Map;

/**
 * Request DTO for creating a new report.
 *
 * LEGACY PATTERNS:
 * - javax.validation.* annotations (target: jakarta.validation.*)
 * - SpringFox annotations (target: springdoc @Schema)
 * - java.util.Date (target: java.time.Instant)
 * - Mutable POJO with setters (target: Java 16+ record)
 */
@ApiModel(description = "Request to generate a new report")
public class ReportRequest {

    @NotBlank(message = "Report name is required")
    @ApiModelProperty(value = "Human-readable name for the report", required = true, example = "Monthly Usage Report")
    private String reportName;

    @NotNull(message = "Report category is required")
    @ApiModelProperty(value = "Category of data to include", required = true)
    private ReportCategory category;

    @NotNull(message = "Report type is required")
    @ApiModelProperty(value = "Output format", required = true, example = "PDF")
    private ReportType reportType;

    @ApiModelProperty(value = "Server-assigned authenticated user ID; client value is ignored", readOnly = true)
    private String requestedBy;

    // LEGACY: java.util.Date
    @ApiModelProperty(value = "Start of reporting period")
    private Date dateFrom;

    // LEGACY: java.util.Date
    @ApiModelProperty(value = "End of reporting period")
    private Date dateTo;

    @ApiModelProperty(value = "Additional parameters for report generation")
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
