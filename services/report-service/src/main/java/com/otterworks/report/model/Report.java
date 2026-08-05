package com.otterworks.report.model;

import io.swagger.v3.oas.annotations.media.Schema;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import jakarta.persistence.Temporal;
import jakarta.persistence.TemporalType;
import jakarta.validation.constraints.NotNull;
import java.util.Date;

/**
 * JPA entity representing a generated report.
 *
 * LEGACY PATTERNS:
 * - java.util.Date fields (target: java.time.Instant / LocalDateTime)
 * - No Lombok — uses manual getters/setters (verbose but explicit)
 */
@Entity
@Table(name = "reports")
@Schema(description = "Generated report metadata")
public class Report {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Schema(description = "Unique report identifier", accessMode = Schema.AccessMode.READ_ONLY)
    private Long id;

    @NotNull
    @Column(name = "report_name", nullable = false)
    @Schema(description = "Human-readable report name", requiredMode = Schema.RequiredMode.REQUIRED)
    private String reportName;

    @NotNull
    @Enumerated(EnumType.STRING)
    @Column(name = "category", nullable = false)
    @Schema(description = "Report category", requiredMode = Schema.RequiredMode.REQUIRED)
    private ReportCategory category;

    @NotNull
    @Enumerated(EnumType.STRING)
    @Column(name = "report_type", nullable = false)
    @Schema(description = "Output format: PDF, CSV, or EXCEL", requiredMode = Schema.RequiredMode.REQUIRED)
    private ReportType reportType;

    @NotNull
    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false)
    @Schema(description = "Current generation status", accessMode = Schema.AccessMode.READ_ONLY)
    private ReportStatus status;

    @Column(name = "requested_by", nullable = false)
    @Schema(description = "User ID who requested the report")
    private String requestedBy;

    // LEGACY: java.util.Date instead of java.time.Instant
    @Temporal(TemporalType.TIMESTAMP)
    @Column(name = "date_from")
    @Schema(description = "Report data start date")
    private Date dateFrom;

    // LEGACY: java.util.Date instead of java.time.Instant
    @Temporal(TemporalType.TIMESTAMP)
    @Column(name = "date_to")
    @Schema(description = "Report data end date")
    private Date dateTo;

    // LEGACY: java.util.Date instead of java.time.Instant
    @Temporal(TemporalType.TIMESTAMP)
    @Column(name = "created_at", nullable = false)
    @Schema(description = "When the report was requested", accessMode = Schema.AccessMode.READ_ONLY)
    private Date createdAt;

    // LEGACY: java.util.Date instead of java.time.Instant
    @Temporal(TemporalType.TIMESTAMP)
    @Column(name = "completed_at")
    @Schema(description = "When the report finished generating", accessMode = Schema.AccessMode.READ_ONLY)
    private Date completedAt;

    @Column(name = "file_path")
    @Schema(description = "Path to the generated report file")
    private String filePath;

    @Column(name = "file_size_bytes")
    @Schema(description = "Size of the generated file in bytes")
    private Long fileSizeBytes;

    @Column(name = "row_count")
    @Schema(description = "Number of data rows in the report")
    private Integer rowCount;

    @Lob
    @Column(name = "error_message")
    @Schema(description = "Error message if generation failed")
    private String errorMessage;

    @Column(name = "parameters")
    @Schema(description = "JSON-encoded report parameters")
    private String parameters;

    // Default constructor required by JPA
    public Report() {
    }

    // LEGACY: Manual getters and setters (verbose Java 8 style)
    // Modern approach would use records (Java 16+) or Lombok

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

    public ReportStatus getStatus() {
        return status;
    }

    public void setStatus(ReportStatus status) {
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

    public String getFilePath() {
        return filePath;
    }

    public void setFilePath(String filePath) {
        this.filePath = filePath;
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

    public String getErrorMessage() {
        return errorMessage;
    }

    public void setErrorMessage(String errorMessage) {
        this.errorMessage = errorMessage;
    }

    public String getParameters() {
        return parameters;
    }

    public void setParameters(String parameters) {
        this.parameters = parameters;
    }

    // LEGACY: toString() with string concatenation instead of String.format or records
    @Override
    public String toString() {
        return "Report{" +
                "id=" + id +
                ", reportName='" + reportName + '\'' +
                ", category=" + category +
                ", reportType=" + reportType +
                ", status=" + status +
                ", requestedBy='" + requestedBy + '\'' +
                ", createdAt=" + createdAt +
                '}';
    }
}
