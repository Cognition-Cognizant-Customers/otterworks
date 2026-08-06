package com.otterworks.report.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportRequest;
import com.otterworks.report.model.ReportStatus;
import com.otterworks.report.model.ReportType;
import com.otterworks.report.service.ReportService;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.junit4.SpringRunner;
import org.springframework.test.web.servlet.MockMvc;

import java.io.File;
import java.io.IOException;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Status-machine, result-set size, date-range and malformed-input boundaries for
 * {@link ReportController} (WP-12).
 *
 * The {@link ReportService} is mocked so every lifecycle state can be asserted
 * directly. The existing end-to-end suite drives the real asynchronous worker and
 * therefore cannot pin the transient PENDING / GENERATING responses without racing
 * it (see the PR description for the flake this replaces).
 */
@RunWith(SpringRunner.class)
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
public class ReportControllerBoundaryTest {

    private static final long LARGE_REPORT_COUNT = 1000L;

    @Rule
    public TemporaryFolder tempFolder = new TemporaryFolder();

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @MockBean
    private ReportService reportService;

    private Report report;

    @Before
    public void setUp() {
        report = new Report();
        report.setId(42L);
        report.setReportName("Quarterly Usage");
        report.setCategory(ReportCategory.USAGE_ANALYTICS);
        report.setReportType(ReportType.CSV);
        report.setStatus(ReportStatus.COMPLETED);
        report.setRequestedBy("user-001");
        report.setCreatedAt(new Date(1709251200000L));
    }

    private String json(ReportRequest request) throws Exception {
        return objectMapper.writeValueAsString(request);
    }

    private ReportRequest validRequest() {
        ReportRequest request = new ReportRequest();
        request.setReportName("Quarterly Usage");
        request.setCategory(ReportCategory.USAGE_ANALYTICS);
        request.setReportType(ReportType.CSV);
        request.setRequestedBy("user-001");
        return request;
    }

    // ---- download: every lifecycle state ----

    @Test
    public void downloadOfAPendingReportIsAConflict() throws Exception {
        report.setStatus(ReportStatus.PENDING);
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));

        mockMvc.perform(get("/api/v1/reports/42/download")).andExpect(status().isConflict());
    }

    @Test
    public void downloadOfAGeneratingReportIsAConflict() throws Exception {
        report.setStatus(ReportStatus.GENERATING);
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));

        mockMvc.perform(get("/api/v1/reports/42/download")).andExpect(status().isConflict());
    }

    @Test
    public void downloadOfAFailedReportIsNotFound() throws Exception {
        report.setStatus(ReportStatus.FAILED);
        report.setErrorMessage("upstream unavailable");
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));

        mockMvc.perform(get("/api/v1/reports/42/download")).andExpect(status().isNotFound());
    }

    @Test
    public void downloadOfACompletedReportWithNoFilePathIsNotFound() throws Exception {
        report.setFilePath(null);
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));

        mockMvc.perform(get("/api/v1/reports/42/download")).andExpect(status().isNotFound());
    }

    @Test
    public void downloadOfACompletedReportWhoseFileVanishedIsNotFound() throws Exception {
        report.setFilePath(new File(tempFolder.getRoot(), "never-written.csv").getAbsolutePath());
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));

        mockMvc.perform(get("/api/v1/reports/42/download")).andExpect(status().isNotFound());
    }

    @Test
    public void downloadOfAnUnknownIdIsNotFound() throws Exception {
        when(reportService.getReport(anyLong())).thenReturn(Optional.<Report>empty());

        mockMvc.perform(get("/api/v1/reports/999999/download")).andExpect(status().isNotFound());
    }

    @Test
    public void downloadOfACompletedCsvReturnsTheFileAsAnAttachment() throws Exception {
        File file = writeFile("quarterly_usage.csv", "a,b\n1,2\n");
        report.setFilePath(file.getAbsolutePath());
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));

        mockMvc.perform(get("/api/v1/reports/42/download"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith("text/csv"))
                .andExpect(header().string("Content-Disposition", "attachment; filename=\"quarterly_usage.csv\""))
                .andExpect(content().string("a,b\n1,2\n"));
    }

    @Test
    public void downloadContentTypeFollowsTheReportType() throws Exception {
        assertContentTypeFor(ReportType.PDF, "report.pdf", "application/pdf");
        assertContentTypeFor(ReportType.CSV, "report.csv", "text/csv");
        assertContentTypeFor(ReportType.EXCEL, "report.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    }

    private void assertContentTypeFor(ReportType type, String fileName, String expected) throws Exception {
        File file = writeFile(fileName, "payload");
        report.setReportType(type);
        report.setFilePath(file.getAbsolutePath());
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));

        mockMvc.perform(get("/api/v1/reports/42/download"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(expected));
    }

    @Test
    public void downloadOfAnEmptyGeneratedFileStillSucceedsWithZeroLength() throws Exception {
        // Boundary: an empty result set produces a zero-byte CSV, which is a
        // successful download rather than a 404.
        File file = writeFile("empty.csv", "");
        report.setFilePath(file.getAbsolutePath());
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));

        mockMvc.perform(get("/api/v1/reports/42/download"))
                .andExpect(status().isOk())
                .andExpect(content().string(""));
    }

    private File writeFile(String name, String contents) throws IOException {
        File file = tempFolder.newFile(name);
        org.apache.commons.io.FileUtils.writeStringToFile(file, contents, Charset.defaultCharset().name());
        return file;
    }

    // ---- list: result-set size boundaries 0 / 1 / many ----

    @Test
    public void listWithNoFilterDefaultsToCompletedReports() throws Exception {
        when(reportService.getReportsByStatus(ReportStatus.COMPLETED)).thenReturn(Arrays.asList(report));

        mockMvc.perform(get("/api/v1/reports"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1))
                .andExpect(jsonPath("$.reports[0].id").value(42));
    }

    @Test
    public void listReturnsAnEmptyEnvelopeRatherThanA404() throws Exception {
        when(reportService.getReportsByStatus(any(ReportStatus.class))).thenReturn(new ArrayList<Report>());

        mockMvc.perform(get("/api/v1/reports").param("status", "FAILED"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(0))
                .andExpect(jsonPath("$.reports").isEmpty());
    }

    @Test
    public void listReturnsEveryRowOfALargeResultSet() throws Exception {
        List<Report> many = new ArrayList<Report>();
        for (long i = 0; i < LARGE_REPORT_COUNT; i++) {
            Report r = new Report();
            r.setId(i);
            r.setReportName("Report " + i);
            r.setCategory(ReportCategory.AUDIT_LOG);
            r.setReportType(ReportType.PDF);
            r.setStatus(ReportStatus.COMPLETED);
            r.setRequestedBy("user-001");
            many.add(r);
        }
        when(reportService.getReportsByStatus(ReportStatus.COMPLETED)).thenReturn(many);

        mockMvc.perform(get("/api/v1/reports"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value((int) LARGE_REPORT_COUNT))
                .andExpect(jsonPath("$.reports[" + (LARGE_REPORT_COUNT - 1) + "].reportName")
                        .value("Report " + (LARGE_REPORT_COUNT - 1)));
    }

    @Test
    public void listPrefersTheUserFilterWhenBothFiltersAreSupplied() throws Exception {
        when(reportService.getReportsByUser("user-001")).thenReturn(Arrays.asList(report));

        mockMvc.perform(get("/api/v1/reports").param("userId", "user-001").param("status", "FAILED"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1));
    }

    @Test
    public void listRejectsAStatusOutsideTheEnum() throws Exception {
        mockMvc.perform(get("/api/v1/reports").param("status", "NOT_A_STATUS"))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void anyCallerCanListAnotherUsersReportsWithoutAuthenticating() throws Exception {
        // FINDING (WP-12, authz negative): SecurityConfig permits /api/v1/reports/**
        // outright ("TODO: Add JWT validation"), so `?userId=` is an unauthenticated
        // read of somebody else's report metadata. Pinned as today's behaviour; the
        // fix is a production change and out of scope for a test-only package.
        when(reportService.getReportsByUser("someone-else")).thenReturn(Arrays.asList(report));

        mockMvc.perform(get("/api/v1/reports").param("userId", "someone-else"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reports[0].requestedBy").value("user-001"));
    }

    // ---- create: validation and date-range boundaries ----

    @Test
    public void createWithAValidBodyIsAccepted() throws Exception {
        when(reportService.createReport(any(ReportRequest.class))).thenReturn(report);

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json(validRequest())))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("COMPLETED"));
    }

    @Test
    public void createRejectsABlankOrMissingReportName() throws Exception {
        ReportRequest blank = validRequest();
        blank.setReportName("   ");
        expectBadRequest(blank);

        ReportRequest missing = validRequest();
        missing.setReportName(null);
        expectBadRequest(missing);
    }

    @Test
    public void createRejectsAMissingCategoryTypeOrRequester() throws Exception {
        ReportRequest noCategory = validRequest();
        noCategory.setCategory(null);
        expectBadRequest(noCategory);

        ReportRequest noType = validRequest();
        noType.setReportType(null);
        expectBadRequest(noType);

        ReportRequest noRequester = validRequest();
        noRequester.setRequestedBy("");
        expectBadRequest(noRequester);
    }

    private void expectBadRequest(ReportRequest request) throws Exception {
        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json(request)))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void createRejectsMalformedJson() throws Exception {
        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reportName\": \"broken\","))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void createRejectsAnUnknownEnumValue() throws Exception {
        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reportName\":\"x\",\"category\":\"NOT_A_CATEGORY\","
                                + "\"reportType\":\"CSV\",\"requestedBy\":\"user-001\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void createRejectsAnUnparseableDate() throws Exception {
        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reportName\":\"x\",\"category\":\"USAGE_ANALYTICS\","
                                + "\"reportType\":\"CSV\",\"requestedBy\":\"user-001\","
                                + "\"dateFrom\":\"yesterday\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void createRejectsAnEmptyBody() throws Exception {
        mockMvc.perform(post("/api/v1/reports").contentType(MediaType.APPLICATION_JSON).content(""))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void createAcceptsAZeroWidthDateRange() throws Exception {
        when(reportService.createReport(any(ReportRequest.class))).thenReturn(report);
        ReportRequest request = validRequest();
        Date sameInstant = new Date(1709251200000L);
        request.setDateFrom(sameInstant);
        request.setDateTo(sameInstant);

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json(request)))
                .andExpect(status().isAccepted());
    }

    @Test
    public void createAcceptsAnInvertedDateRangeWithoutValidation() throws Exception {
        // FINDING (WP-12, judged genuine): dateFrom > dateTo is accepted and produces
        // an empty report instead of a 400. Pinned rather than fixed -- adding the
        // constraint would be a production change.
        when(reportService.createReport(any(ReportRequest.class))).thenReturn(report);
        ReportRequest request = validRequest();
        request.setDateFrom(new Date(1709337600000L)); // 2024-03-02
        request.setDateTo(new Date(1709251200000L)); // 2024-03-01

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json(request)))
                .andExpect(status().isAccepted());
    }

    @Test
    public void createAcceptsAFarFutureDateRange() throws Exception {
        when(reportService.createReport(any(ReportRequest.class))).thenReturn(report);
        ReportRequest request = validRequest();
        request.setDateFrom(new Date(4102444800000L)); // 2100-01-01
        request.setDateTo(new Date(4133980800000L)); // 2101-01-01

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json(request)))
                .andExpect(status().isAccepted());
    }

    // ---- get / delete ----

    @Test
    public void getAnUnknownReportIsNotFound() throws Exception {
        when(reportService.getReport(anyLong())).thenReturn(Optional.<Report>empty());

        mockMvc.perform(get("/api/v1/reports/999999")).andExpect(status().isNotFound());
    }

    @Test
    public void getRejectsANonNumericId() throws Exception {
        mockMvc.perform(get("/api/v1/reports/not-a-number")).andExpect(status().isBadRequest());
    }

    @Test
    public void getExposesADownloadUrlOnlyOnceAFilePathExists() throws Exception {
        when(reportService.getReport(42L)).thenReturn(Optional.of(report));
        mockMvc.perform(get("/api/v1/reports/42"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.downloadUrl").doesNotExist());

        report.setFilePath("/tmp/reports/quarterly.csv");
        mockMvc.perform(get("/api/v1/reports/42"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.downloadUrl").value("/api/v1/reports/42/download"));
    }

    @Test
    public void deleteIsIdempotentFromTheClientsPointOfViewOnlyForTheFirstCall() throws Exception {
        // Boundary between 204 and 404: the second delete of the same id reports 404,
        // so DELETE is not idempotent at the HTTP-status level.
        when(reportService.deleteReport(42L)).thenReturn(true, false);

        mockMvc.perform(delete("/api/v1/reports/42")).andExpect(status().isNoContent());
        mockMvc.perform(delete("/api/v1/reports/42")).andExpect(status().isNotFound());
    }

    @Test
    public void deleteOfAnUnknownReportIsNotFound() throws Exception {
        when(reportService.deleteReport(eq(999999L))).thenReturn(false);

        mockMvc.perform(delete("/api/v1/reports/999999")).andExpect(status().isNotFound());
    }
}
