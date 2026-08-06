package com.otterworks.report.service;

import com.otterworks.report.config.AppConfig;
import com.otterworks.report.model.Report;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportRequest;
import com.otterworks.report.model.ReportStatus;
import com.otterworks.report.model.ReportType;
import com.otterworks.report.repository.ReportRepository;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Date-range defaulting, parameter handling and delete semantics for
 * {@link ReportService} (WP-12).
 *
 * The repository and the async worker are mocked and the transaction
 * synchronisation registry is driven by hand, so nothing here touches a
 * database, a thread pool, or the clock beyond asserting relative ordering.
 */
public class ReportServiceUnitTest {

    private static final long THIRTY_DAYS_MS = 30L * 24 * 3600 * 1000;

    private ReportRepository repository;
    private ReportGenerationWorker worker;
    private ReportService service;

    @Before
    public void setUp() {
        repository = mock(ReportRepository.class);
        worker = mock(ReportGenerationWorker.class);
        service = new ReportService(repository, worker, new AppConfig());

        when(repository.save(any(Report.class))).thenAnswer(invocation -> {
            Report r = invocation.getArgument(0);
            r.setId(7L);
            return r;
        });

        // createReport / deleteReport register an afterCommit callback, which requires
        // an active synchronisation. Real requests get one from @Transactional.
        TransactionSynchronizationManager.initSynchronization();
    }

    @After
    public void tearDown() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
    }

    private ReportRequest request() {
        ReportRequest request = new ReportRequest();
        request.setReportName("Quarterly Usage");
        request.setCategory(ReportCategory.USAGE_ANALYTICS);
        request.setReportType(ReportType.CSV);
        request.setRequestedBy("user-001");
        return request;
    }

    private Report savedReport() {
        ArgumentCaptor<Report> captor = ArgumentCaptor.forClass(Report.class);
        verify(repository).save(captor.capture());
        return captor.getValue();
    }

    private void commit() {
        List<TransactionSynchronization> callbacks =
                new ArrayList<TransactionSynchronization>(TransactionSynchronizationManager.getSynchronizations());
        for (TransactionSynchronization callback : callbacks) {
            callback.afterCommit();
        }
    }

    // ---- date-range defaulting boundaries ----

    @Test
    public void anAbsentDateRangeDefaultsToTheLastThirtyDays() {
        long before = System.currentTimeMillis();
        service.createReport(request());
        long after = System.currentTimeMillis();

        Report saved = savedReport();
        long span = saved.getDateTo().getTime() - saved.getDateFrom().getTime();

        assertTrue("span was " + span, Math.abs(span - THIRTY_DAYS_MS) <= (after - before) + 1000L);
        assertTrue(saved.getDateTo().getTime() >= before);
        assertTrue(saved.getDateTo().getTime() <= after);
    }

    @Test
    public void anExplicitDateRangeIsKeptVerbatim() {
        ReportRequest request = request();
        Date from = new Date(1709251200000L);
        Date to = new Date(1709337600000L);
        request.setDateFrom(from);
        request.setDateTo(to);

        service.createReport(request);

        Report saved = savedReport();
        assertEquals(from, saved.getDateFrom());
        assertEquals(to, saved.getDateTo());
    }

    @Test
    public void onlyTheMissingEndOfAPartialRangeIsDefaulted() {
        ReportRequest request = request();
        Date from = new Date(1709251200000L);
        request.setDateFrom(from);

        service.createReport(request);

        Report saved = savedReport();
        assertEquals(from, saved.getDateFrom());
        assertNotNull(saved.getDateTo());
        assertTrue(saved.getDateTo().after(from));
    }

    @Test
    public void anInvertedRangeIsPersistedUnchallenged() {
        // FINDING (WP-12): no dateFrom <= dateTo validation anywhere in the chain.
        ReportRequest request = request();
        request.setDateFrom(new Date(1709337600000L));
        request.setDateTo(new Date(1709251200000L));

        service.createReport(request);

        Report saved = savedReport();
        assertTrue(saved.getDateFrom().after(saved.getDateTo()));
    }

    // ---- parameters ----

    @Test
    public void absentParametersLeaveTheColumnNull() {
        service.createReport(request());

        assertNull(savedReport().getParameters());
    }

    @Test
    public void emptyParametersSerialiseToAnEmptyJsonObject() {
        ReportRequest request = request();
        request.setParameters(new LinkedHashMap<String, String>());

        service.createReport(request);

        assertEquals("{}", savedReport().getParameters());
    }

    @Test
    public void parametersAreSerialisedAsJson() {
        ReportRequest request = request();
        Map<String, String> parameters = new LinkedHashMap<String, String>();
        parameters.put("metric", "page_views");
        parameters.put("granularity", "daily");
        request.setParameters(parameters);

        service.createReport(request);

        assertEquals("{\"metric\":\"page_views\",\"granularity\":\"daily\"}", savedReport().getParameters());
    }

    // ---- lifecycle ----

    @Test
    public void aNewReportStartsPendingAndOnlyDispatchesAfterCommit() {
        Report created = service.createReport(request());

        assertEquals(ReportStatus.PENDING, created.getStatus());
        verify(worker, never()).generateReportAsync(anyLong());

        commit();
        verify(worker, times(1)).generateReportAsync(7L);
    }

    @Test
    public void creatingAReportOutsideATransactionFailsFast() {
        // Negative case: the afterCommit hook needs an active synchronisation, so
        // calling createReport without @Transactional throws rather than silently
        // skipping generation.
        TransactionSynchronizationManager.clearSynchronization();

        try {
            service.createReport(request());
            fail("expected IllegalStateException");
        } catch (IllegalStateException expected) {
            assertNotNull(expected.getMessage());
        }
    }

    @Test
    public void gettersDelegateStraightToTheRepository() {
        Report report = new Report();
        report.setId(7L);
        when(repository.findById(7L)).thenReturn(Optional.of(report));
        when(repository.findByRequestedByOrderByCreatedAtDesc("user-001")).thenReturn(Arrays.asList(report));
        when(repository.findByStatusOrderByCreatedAtAsc(ReportStatus.FAILED)).thenReturn(new ArrayList<Report>());

        assertTrue(service.getReport(7L).isPresent());
        assertEquals(1, service.getReportsByUser("user-001").size());
        assertTrue(service.getReportsByStatus(ReportStatus.FAILED).isEmpty());
    }

    @Test
    public void getReportOfAnUnknownIdIsEmpty() {
        when(repository.findById(anyLong())).thenReturn(Optional.<Report>empty());

        assertFalse(service.getReport(999999L).isPresent());
    }

    @Test
    public void deletingAnUnknownReportReturnsFalseAndTouchesNothing() {
        when(repository.findById(999999L)).thenReturn(Optional.<Report>empty());

        assertFalse(service.deleteReport(999999L));
        verify(repository, never()).deleteById(anyLong());
    }

    @Test
    public void deletingAReportWithNoFileStillRemovesTheRow() {
        Report report = new Report();
        report.setId(7L);
        report.setFilePath(null);
        when(repository.findById(7L)).thenReturn(Optional.of(report));

        assertTrue(service.deleteReport(7L));
        verify(repository).deleteById(7L);
        commit(); // no file callback registered; must not blow up
    }
}
