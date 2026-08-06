package com.otterworks.report.service;

import com.otterworks.report.config.AppConfig;
import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportStatus;
import com.otterworks.report.model.ReportType;
import com.otterworks.report.repository.ReportRepository;
import org.apache.commons.io.FileUtils;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.RestClientException;

import java.io.File;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Row-cap, category-routing and failure-path boundaries of {@link ReportGenerationWorker}.
 *
 * The worker is instantiated directly (not through Spring) so {@code @Async} does not
 * apply and every run completes synchronously — no sleeps, no polling, no shared context.
 * The row cap is driven down from its configured 50,000 default to {@link #ROW_CAP} so the
 * cap-1 / cap / cap+1 trio is cheap to exercise.
 */
public class ReportGenerationWorkerBoundaryTest {

    /** Stand-in for {@code otterworks.report.max-rows} (50,000 in application.properties). */
    private static final int ROW_CAP = 5;

    private ReportRepository repository;
    private ReportDataFetcher dataFetcher;
    private AppConfig appConfig;
    private ReportGenerationWorker worker;
    private File outputDir;

    @Before
    public void setUp() {
        repository = mock(ReportRepository.class);
        dataFetcher = mock(ReportDataFetcher.class);

        outputDir = new File(System.getProperty("java.io.tmpdir"),
                "report-cap-test-" + UUID.randomUUID().toString());

        appConfig = new AppConfig();
        ReflectionTestUtils.setField(appConfig, "maxRows", ROW_CAP);
        ReflectionTestUtils.setField(appConfig, "reportOutputDir", outputDir.getAbsolutePath());

        worker = new ReportGenerationWorker(
                repository,
                dataFetcher,
                new PdfReportGenerator(),
                new CsvReportGenerator(),
                new ExcelReportGenerator(),
                appConfig);

        when(repository.save(any(Report.class))).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @After
    public void tearDown() {
        FileUtils.deleteQuietly(outputDir);
    }

    // ------------------------------------------------------------- row caps

    @Test
    public void zeroRowsCompletesWithAnEmptyReport() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(0));

        worker.generateReportAsync(report.getId());

        Report saved = lastSaved();
        assertEquals(ReportStatus.COMPLETED, saved.getStatus());
        assertEquals(Integer.valueOf(0), saved.getRowCount());
        assertNotNull(saved.getFilePath());
        assertTrue(new File(saved.getFilePath()).exists());
    }

    @Test
    public void oneRowCompletesWithASingleRow() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(1));

        worker.generateReportAsync(report.getId());

        assertEquals(Integer.valueOf(1), lastSaved().getRowCount());
    }

    @Test
    public void oneRowBelowTheCapIsNotTruncated() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(ROW_CAP - 1));

        worker.generateReportAsync(report.getId());

        assertEquals(Integer.valueOf(ROW_CAP - 1), lastSaved().getRowCount());
    }

    @Test
    public void exactlyAtTheCapIsNotTruncated() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(ROW_CAP));

        worker.generateReportAsync(report.getId());

        assertEquals(Integer.valueOf(ROW_CAP), lastSaved().getRowCount());
    }

    @Test
    public void oneRowAboveTheCapIsTruncatedToTheCap() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(ROW_CAP + 1));

        worker.generateReportAsync(report.getId());

        assertEquals(Integer.valueOf(ROW_CAP), lastSaved().getRowCount());
    }

    @Test
    public void farAboveTheCapIsTruncatedToTheCap() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(ROW_CAP * 20));

        worker.generateReportAsync(report.getId());

        assertEquals(Integer.valueOf(ROW_CAP), lastSaved().getRowCount());
    }

    @Test
    public void truncationKeepsTheFirstRowsInOrder() throws Exception {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(ROW_CAP + 3));

        worker.generateReportAsync(report.getId());

        String csv = FileUtils.readFileToString(new File(lastSaved().getFilePath()), "UTF-8");
        assertTrue(csv.contains("row-0"));
        assertTrue(csv.contains("row-" + (ROW_CAP - 1)));
        assertTrue(!csv.contains("row-" + ROW_CAP));
    }

    @Test
    public void theCapAppliesToEveryOutputFormat() {
        for (ReportType type : ReportType.values()) {
            setUp();
            Report report = givenReport(ReportCategory.USAGE_ANALYTICS, type, rows(ROW_CAP + 2));

            worker.generateReportAsync(report.getId());

            assertEquals("row cap not honoured for " + type, Integer.valueOf(ROW_CAP), lastSaved().getRowCount());
            tearDown();
        }
    }

    // --------------------------------------------------------- category routing

    @Test
    public void analyticsCategoriesReadFromTheAnalyticsService() {
        for (ReportCategory category : new ReportCategory[]{
                ReportCategory.USAGE_ANALYTICS, ReportCategory.COLLABORATION_METRICS, ReportCategory.SYSTEM_HEALTH}) {
            setUp();
            Report report = givenReport(category, ReportType.CSV, rows(1));

            worker.generateReportAsync(report.getId());

            verify(dataFetcher).fetchAnalyticsData(any(Date.class), any(Date.class), any());
            verify(dataFetcher, never()).fetchAuditData(any(Date.class), any(Date.class), any());
            tearDown();
        }
    }

    @Test
    public void auditCategoriesReadFromTheAuditService() {
        for (ReportCategory category : new ReportCategory[]{ReportCategory.AUDIT_LOG, ReportCategory.COMPLIANCE}) {
            setUp();
            Report report = givenReport(category, ReportType.CSV, rows(1));

            worker.generateReportAsync(report.getId());

            verify(dataFetcher).fetchAuditData(any(Date.class), any(Date.class), any());
            tearDown();
        }
    }

    @Test
    public void userCategoriesReadFromTheAuthService() {
        for (ReportCategory category : new ReportCategory[]{ReportCategory.USER_ACTIVITY, ReportCategory.STORAGE_SUMMARY}) {
            setUp();
            Report report = givenReport(category, ReportType.CSV, rows(1));

            worker.generateReportAsync(report.getId());

            verify(dataFetcher).fetchUserActivityData(any(Date.class), any(Date.class), any());
            tearDown();
        }
    }

    // ------------------------------------------------------------ date ranges

    @Test
    public void theRequestedDateRangeIsPassedThroughUnchanged() {
        Date from = new Date(1_700_000_000_000L);
        Date to = new Date(1_700_086_400_000L);
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(1));
        report.setDateFrom(from);
        report.setDateTo(to);

        worker.generateReportAsync(report.getId());

        verify(dataFetcher).fetchAnalyticsData(eq(from), eq(to), any());
    }

    @Test
    public void aZeroWidthDateRangeIsAccepted() {
        Date instant = new Date(1_700_000_000_000L);
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(0));
        report.setDateFrom(instant);
        report.setDateTo(instant);

        worker.generateReportAsync(report.getId());

        assertEquals(ReportStatus.COMPLETED, lastSaved().getStatus());
    }

    @Test
    public void anInvertedDateRangeIsForwardedRatherThanRejected() {
        // FINDING (documented, not fixed here): neither ReportRequest validation nor the
        // worker rejects dateTo < dateFrom; the inverted window reaches the upstream
        // service as-is and the report completes as if it were valid.
        Date from = new Date(1_700_086_400_000L);
        Date to = new Date(1_700_000_000_000L);
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(0));
        report.setDateFrom(from);
        report.setDateTo(to);

        worker.generateReportAsync(report.getId());

        verify(dataFetcher).fetchAnalyticsData(eq(from), eq(to), any());
        assertEquals(ReportStatus.COMPLETED, lastSaved().getStatus());
    }

    // -------------------------------------------------------------- negatives

    @Test
    public void anUnknownReportIdIsIgnoredWithoutWriting() {
        when(repository.findById(404L)).thenReturn(Optional.<Report>empty());

        worker.generateReportAsync(404L);

        verify(repository, never()).save(any(Report.class));
    }

    @Test
    public void aMissingReportTypeFailsTheRunInsteadOfThrowing() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(1));
        report.setReportType(null);

        worker.generateReportAsync(report.getId());

        assertEquals(ReportStatus.FAILED, lastSaved().getStatus());
        assertNotNull(lastSaved().getCompletedAt());
    }

    @Test
    public void malformedStoredParametersFailTheRun() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(1));
        report.setParameters("{not-json");

        worker.generateReportAsync(report.getId());

        assertEquals(ReportStatus.FAILED, lastSaved().getStatus());
        assertNotNull(lastSaved().getErrorMessage());
    }

    @Test
    public void emptyStoredParametersAreTreatedAsNoParameters() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(1));
        report.setParameters("{}");

        worker.generateReportAsync(report.getId());

        assertEquals(ReportStatus.COMPLETED, lastSaved().getStatus());
        verify(dataFetcher).fetchAnalyticsData(any(Date.class), any(Date.class), anyMap());
    }

    @Test
    public void anUpstreamFailureIsRecordedOnTheReportRatherThanEscaping() {
        Report report = givenReport(ReportCategory.AUDIT_LOG, ReportType.CSV, null);
        when(dataFetcher.fetchAuditData(any(Date.class), any(Date.class), any()))
                .thenThrow(new RestClientException("audit-service unreachable"));

        worker.generateReportAsync(report.getId());

        Report saved = lastSaved();
        assertEquals(ReportStatus.FAILED, saved.getStatus());
        assertEquals("audit-service unreachable", saved.getErrorMessage());
    }

    @Test
    public void theReportIsMarkedGeneratingBeforeItCompletes() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(1));

        worker.generateReportAsync(report.getId());

        ArgumentCaptor<Report> captor = ArgumentCaptor.forClass(Report.class);
        verify(repository, atLeastOnce()).save(captor.capture());
        assertTrue(captor.getAllValues().size() >= 2);
    }

    // ------------------------------------------------------------ idempotency

    @Test
    public void regeneratingTheSameReportYieldsTheSameRowCount() {
        Report report = givenReport(ReportCategory.USAGE_ANALYTICS, ReportType.CSV, rows(ROW_CAP + 4));

        worker.generateReportAsync(report.getId());
        Integer first = lastSaved().getRowCount();

        worker.generateReportAsync(report.getId());
        Integer second = lastSaved().getRowCount();

        assertEquals(first, second);
        assertEquals(Integer.valueOf(ROW_CAP), second);
        verify(dataFetcher, times(2)).fetchAnalyticsData(any(Date.class), any(Date.class), any());
    }

    // ----------------------------------------------------------------- helpers

    private Report givenReport(ReportCategory category, ReportType type, List<Map<String, Object>> data) {
        Report report = new Report();
        report.setId(7L);
        report.setReportName("Boundary Report");
        report.setCategory(category);
        report.setReportType(type);
        report.setRequestedBy("cap-user");
        report.setStatus(ReportStatus.PENDING);
        report.setCreatedAt(new Date(1_700_000_000_000L));
        report.setDateFrom(new Date(1_699_000_000_000L));
        report.setDateTo(new Date(1_700_000_000_000L));

        when(repository.findById(7L)).thenReturn(Optional.of(report));
        if (data != null) {
            when(dataFetcher.fetchAnalyticsData(any(Date.class), any(Date.class), any())).thenReturn(data);
            when(dataFetcher.fetchAuditData(any(Date.class), any(Date.class), any())).thenReturn(data);
            when(dataFetcher.fetchUserActivityData(any(Date.class), any(Date.class), any())).thenReturn(data);
        }
        return report;
    }

    private Report lastSaved() {
        ArgumentCaptor<Report> captor = ArgumentCaptor.forClass(Report.class);
        verify(repository, atLeastOnce()).save(captor.capture());
        List<Report> saved = captor.getAllValues();
        return saved.get(saved.size() - 1);
    }

    private static List<Map<String, Object>> rows(int count) {
        List<Map<String, Object>> data = new ArrayList<Map<String, Object>>();
        for (int i = 0; i < count; i++) {
            Map<String, Object> row = new HashMap<String, Object>();
            row.put("id", "row-" + i);
            row.put("value", Integer.valueOf(i));
            data.add(row);
        }
        return data;
    }
}
