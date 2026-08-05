package com.otterworks.report.model;

import io.swagger.v3.oas.annotations.media.Schema;

import java.util.Date;

/**
 * Response DTO for report metadata.
 *
 * LEGACY: Uses mutable POJO pattern. Modern Java would use a record.
 */
@Schema(description = "Report metadata response")
public class ReportResponse {

    @Schema(description = "Report ID")
    private Long id;

    @Schema(description = "Report name")
    private String reportName;

    @Schema(description = "Report category")
    private String category;

    @Schema(description = "Output format")
    private String reportType;

    @Schema(description = "Generation status")
    private String status;

    @Schema(description = "Who requested it")
    private String requestedBy;

    @Schema(description = "Data start date")
    private Date dateFrom;

    @Schema(description = "Data end date")
    private Date dateTo;

    @Schema(description = "Request timestamp")
    private Date createdAt;

    @Schema(description = "Completion timestamp")
    private Date completedAt;

    @Schema(description = "File size in bytes")
    private Long fileSizeBytes;

    @Schema(description = "Number of rows")
    private Integer rowCount;

    @Schema(description = "Download URL")
    private String downloadUrl;

    @Schema(description = "Error message if failed")
    private String errorMessage;

    public ReportResponse() {
    }

    // LEGACY: Static factory method with manual field mapping
    // Modern approach: MapStruct or record constructor
    public static ReportResponse fromEntity(Report report) {
        ReportResponse response = new ReportResponse();
        response.setId(report.getId());
        response.setReportName(report.getReportName());
        response.setCategory(report.getCategory() != null ? report.getCategory().name() : null);
        response.setReportType(report.getReportType() != null ? report.getReportType().name() : null);
        response.setStatus(report.getStatus() != null ? report.getStatus().name() : null);
        response.setRequestedBy(report.getRequestedBy());
        response.setDateFrom(report.getDateFrom());
        response.setDateTo(report.getDateTo());
        response.setCreatedAt(report.getCreatedAt());
        response.setCompletedAt(report.getCompletedAt());
        response.setFileSizeBytes(report.getFileSizeBytes());
        response.setRowCount(report.getRowCount());
        if (report.getFilePath() != null) {
            response.setDownloadUrl("/api/v1/reports/" + report.getId() + "/download");
        }
        response.setErrorMessage(report.getErrorMessage());
        return response;
    }

    // Getters and setters
    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getReportName() {
        return reportName;
    }

    public void setReportName(String reportName) {
        this.reportName = reportName;
    }

    public String getCategory() {
        return category;
    }

    public void setCategory(String category) {
        this.category = category;
    }

    public String getReportType() {
        return reportType;
    }

    public void setReportType(String reportType) {
        this.reportType = reportType;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
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

    public Date getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Date createdAt) {
        this.createdAt = createdAt;
    }

    public Date getCompletedAt() {
        return completedAt;
    }

    public void setCompletedAt(Date completedAt) {
        this.completedAt = completedAt;
    }

    public Long getFileSizeBytes() {
        return fileSizeBytes;
    }

    public void setFileSizeBytes(Long fileSizeBytes) {
        this.fileSizeBytes = fileSizeBytes;
    }

    public Integer getRowCount() {
        return rowCount;
    }

    public void setRowCount(Integer rowCount) {
        this.rowCount = rowCount;
    }

    public String getDownloadUrl() {
        return downloadUrl;
    }

    public void setDownloadUrl(String downloadUrl) {
        this.downloadUrl = downloadUrl;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public void setErrorMessage(String errorMessage) {
        this.errorMessage = errorMessage;
    }
}
