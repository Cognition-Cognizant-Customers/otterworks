package com.otterworks.report.controller;

import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportRequest;
import com.otterworks.report.model.ReportResponse;
import com.otterworks.report.model.ReportStatus;
import com.otterworks.report.service.ReportService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.apache.commons.io.FileUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import jakarta.validation.Valid;
import java.io.File;
import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

/**
 * REST controller for report management.
 *
 * LEGACY PATTERNS:
 * - Commons IO FileUtils for file reading (target: Files.readAllBytes or streaming)
 * - ByteArrayResource loads entire file into memory (target: InputStreamResource for streaming)
 * - No pagination on list endpoint
 * - Manual response mapping without MapStruct or similar
 */
@RestController
@RequestMapping("/api/v1/reports")
@Tag(name = "Reports", description = "Report generation and management")
public class ReportController {

    private static final Logger logger = LoggerFactory.getLogger(ReportController.class);

    private final ReportService reportService;

    public ReportController(ReportService reportService) {
        this.reportService = reportService;
    }

    @PostMapping
    @Operation(summary = "Create a new report", description = "Submits a report generation request. The report is generated asynchronously.")
    @ApiResponses({
            @ApiResponse(responseCode = "202", description = "Report request accepted"),
            @ApiResponse(responseCode = "400", description = "Invalid request")
    })
    public ResponseEntity<ReportResponse> createReport(
            @Valid @RequestBody ReportRequest request) {

        logger.info("Report request: name={}, category={}, type={}, by={}",
                request.getReportName(), request.getCategory(),
                request.getReportType(), request.getRequestedBy());

        Report report = reportService.createReport(request);
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .body(ReportResponse.fromEntity(report));
    }

    @GetMapping("/{id}")
    @Operation(summary = "Get report by ID", description = "Returns the report metadata and status")
    @ApiResponses({
            @ApiResponse(responseCode = "200", description = "Report found"),
            @ApiResponse(responseCode = "404", description = "Report not found")
    })
    public ResponseEntity<ReportResponse> getReport(
            @Parameter(description = "Report ID", required = true)
            @PathVariable Long id) {

        Optional<Report> report = reportService.getReport(id);
        if (!report.isPresent()) { // LEGACY: !isPresent() instead of isEmpty()
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(ReportResponse.fromEntity(report.get()));
    }

    @GetMapping
    @Operation(summary = "List reports", description = "List reports filtered by user ID or status")
    public ResponseEntity<Map<String, Object>> listReports(
            @Parameter(description = "Filter by user ID")
            @RequestParam(required = false) String userId,
            @Parameter(description = "Filter by status")
            @RequestParam(required = false) ReportStatus status) {

        List<Report> reports;
        if (userId != null) {
            reports = reportService.getReportsByUser(userId);
        } else if (status != null) {
            reports = reportService.getReportsByStatus(status);
        } else {
            reports = reportService.getReportsByStatus(ReportStatus.COMPLETED);
        }

        List<ReportResponse> responses = reports.stream()
                .map(ReportResponse::fromEntity)
                .collect(Collectors.toList()); // LEGACY: .toList() available in Java 16+

        // LEGACY: Manual response wrapper instead of a proper Page/Slice object
        Map<String, Object> response = new HashMap<>();
        response.put("reports", responses);
        response.put("total", responses.size());

        return ResponseEntity.ok(response);
    }

    @GetMapping("/{id}/download")
    @Operation(summary = "Download a generated report", description = "Returns the report file for download")
    @ApiResponses({
            @ApiResponse(responseCode = "200", description = "Report file"),
            @ApiResponse(responseCode = "404", description = "Report not found or not yet completed"),
            @ApiResponse(responseCode = "409", description = "Report is still generating")
    })
    public ResponseEntity<Resource> downloadReport(
            @Parameter(description = "Report ID", required = true)
            @PathVariable Long id) {

        Optional<Report> optReport = reportService.getReport(id);
        if (!optReport.isPresent()) {
            return ResponseEntity.notFound().build();
        }

        Report report = optReport.get();

        if (report.getStatus() == ReportStatus.GENERATING || report.getStatus() == ReportStatus.PENDING) {
            return ResponseEntity.status(HttpStatus.CONFLICT).build();
        }

        if (report.getStatus() == ReportStatus.FAILED || report.getFilePath() == null) {
            return ResponseEntity.notFound().build();
        }

        File file = new File(report.getFilePath());
        if (!file.exists()) {
            logger.warn("Report file missing: {}", report.getFilePath());
            return ResponseEntity.notFound().build();
        }

        try {
            // LEGACY: Commons IO FileUtils.readFileToByteArray loads entire file into memory
            // Modern approach: InputStreamResource with streaming, or S3 presigned URL
            byte[] fileContent = FileUtils.readFileToByteArray(file);
            ByteArrayResource resource = new ByteArrayResource(fileContent);

            String contentType = getContentType(report.getReportType());
            String fileName = file.getName();

            return ResponseEntity.ok()
                    .contentType(MediaType.parseMediaType(contentType))
                    .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + fileName + "\"")
                    .contentLength(fileContent.length)
                    .body(resource);

        } catch (IOException e) {
            logger.error("Failed to read report file {}: {}", report.getFilePath(), e.getMessage());
            return ResponseEntity.notFound().build();
        }
    }

    @DeleteMapping("/{id}")
    @Operation(summary = "Delete a report", description = "Deletes the report record and its generated file")
    @ApiResponses({
            @ApiResponse(responseCode = "204", description = "Report deleted"),
            @ApiResponse(responseCode = "404", description = "Report not found")
    })
    public ResponseEntity<Void> deleteReport(
            @Parameter(description = "Report ID", required = true)
            @PathVariable Long id) {

        boolean deleted = reportService.deleteReport(id);
        if (!deleted) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.noContent().build();
    }

    // ----- Private helpers -----

    private String getContentType(com.otterworks.report.model.ReportType reportType) {
        // LEGACY: switch without enhanced syntax
        switch (reportType) {
            case PDF:
                return "application/pdf";
            case CSV:
                return "text/csv";
            case EXCEL:
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
            default:
                return "application/octet-stream";
        }
    }
}
