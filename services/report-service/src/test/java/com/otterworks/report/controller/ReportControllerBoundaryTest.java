package com.otterworks.report.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.report.config.AppConfig;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportRequest;
import com.otterworks.report.model.ReportType;
import org.junit.Ignore;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.junit4.SpringRunner;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.util.Date;
import java.util.HashMap;
import java.util.Map;

import static org.hamcrest.Matchers.is;
import static org.hamcrest.Matchers.notNullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Parameter, identifier and cross-user boundaries of {@link ReportController}.
 *
 * Every test owns a unique requester id, so no assertion depends on another test's
 * rows in the shared in-memory H2 database or on the fire-and-forget generation
 * worker having finished. Only fields that generation never rewrites (id, name,
 * category, type, requester, requested window) are asserted.
 *
 * Written in JUnit 4 to match the module's stack.
 */
@RunWith(SpringRunner.class)
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
public class ReportControllerBoundaryTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private AppConfig appConfig;

    // ------------------------------------------------------- malformed requests

    @Test
    public void anEmptyBodyIsRejected() throws Exception {
        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(""))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void syntacticallyBrokenJsonIsRejected() throws Exception {
        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reportName\": \"Broken\","))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void aJsonArrayInsteadOfAnObjectIsRejected() throws Exception {
        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("[]"))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void aRequestWithoutAContentTypeIsRejected() throws Exception {
        ReportRequest request = buildRequest("No Content Type", ReportCategory.USAGE_ANALYTICS,
                ReportType.CSV, "boundary-user-ct");

        mockMvc.perform(post("/api/v1/reports").content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isUnsupportedMediaType());
    }

    @Test
    public void aWhitespaceOnlyReportNameIsRejected() throws Exception {
        ReportRequest request = buildRequest("   ", ReportCategory.USAGE_ANALYTICS,
                ReportType.CSV, "boundary-user-blank-name");

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void aBlankRequesterIsRejected() throws Exception {
        ReportRequest request = buildRequest("No Requester", ReportCategory.USAGE_ANALYTICS,
                ReportType.CSV, "  ");

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void anUnknownReportCategoryIsRejected() throws Exception {
        String body = "{\"reportName\":\"Bad Category\",\"category\":\"NOT_A_CATEGORY\","
                + "\"reportType\":\"CSV\",\"requestedBy\":\"boundary-user-cat\"}";

        mockMvc.perform(post("/api/v1/reports").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void anUnknownReportTypeIsRejected() throws Exception {
        String body = "{\"reportName\":\"Bad Type\",\"category\":\"USAGE_ANALYTICS\","
                + "\"reportType\":\"XML\",\"requestedBy\":\"boundary-user-type\"}";

        mockMvc.perform(post("/api/v1/reports").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void aLowercaseReportTypeIsRejectedRatherThanCoerced() throws Exception {
        String body = "{\"reportName\":\"Lowercase Type\",\"category\":\"USAGE_ANALYTICS\","
                + "\"reportType\":\"csv\",\"requestedBy\":\"boundary-user-lower\"}";

        mockMvc.perform(post("/api/v1/reports").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void anUnparseableDateIsRejected() throws Exception {
        String body = "{\"reportName\":\"Bad Date\",\"category\":\"USAGE_ANALYTICS\","
                + "\"reportType\":\"CSV\",\"requestedBy\":\"boundary-user-date\",\"dateFrom\":\"yesterday\"}";

        mockMvc.perform(post("/api/v1/reports").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void unknownExtraFieldsAreIgnoredRatherThanRejected() throws Exception {
        String body = "{\"reportName\":\"Extra Fields\",\"category\":\"USAGE_ANALYTICS\","
                + "\"reportType\":\"CSV\",\"requestedBy\":\"boundary-user-extra\",\"nonsense\":42}";

        mockMvc.perform(post("/api/v1/reports").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isAccepted());
    }

    // ------------------------------------------------------------- date ranges

    @Test
    public void anExplicitDateRangeIsEchoedBack() throws Exception {
        ReportRequest request = buildRequest("Explicit Range", ReportCategory.USAGE_ANALYTICS,
                ReportType.CSV, "boundary-user-range");
        request.setDateFrom(new Date(1_700_000_000_000L));
        request.setDateTo(new Date(1_700_086_400_000L));

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.dateFrom", notNullValue()))
                .andExpect(jsonPath("$.dateTo", notNullValue()));
    }

    @Test
    public void aZeroWidthDateRangeIsAccepted() throws Exception {
        Date instant = new Date(1_700_000_000_000L);
        ReportRequest request = buildRequest("Zero Width Range", ReportCategory.USAGE_ANALYTICS,
                ReportType.CSV, "boundary-user-zero-range");
        request.setDateFrom(instant);
        request.setDateTo(instant);

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isAccepted());
    }

    @Test
    public void anInvertedDateRangeIsCurrentlyAccepted() throws Exception {
        // FINDING (documented, not fixed here): ReportRequest carries no cross-field
        // validation, so dateTo < dateFrom is accepted and generated as if valid.
        ReportRequest request = buildRequest("Inverted Range", ReportCategory.USAGE_ANALYTICS,
                ReportType.CSV, "boundary-user-inverted");
        request.setDateFrom(new Date(1_700_086_400_000L));
        request.setDateTo(new Date(1_700_000_000_000L));

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isAccepted());
    }

    @Test
    @Ignore("FINDING: no cross-field validation on ReportRequest — an inverted date range should be a 400")
    public void anInvertedDateRangeShouldBeRejected() throws Exception {
        ReportRequest request = buildRequest("Inverted Range Rejected", ReportCategory.USAGE_ANALYTICS,
                ReportType.CSV, "boundary-user-inverted-2");
        request.setDateFrom(new Date(1_700_086_400_000L));
        request.setDateTo(new Date(1_700_000_000_000L));

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void omittingTheDateRangeFallsBackToTheDefaultWindow() throws Exception {
        ReportRequest request = buildRequest("Default Range", ReportCategory.USAGE_ANALYTICS,
                ReportType.CSV, "boundary-user-default-range");

        mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.dateFrom", notNullValue()))
                .andExpect(jsonPath("$.dateTo", notNullValue()));
    }

    // ------------------------------------------------------------- identifiers

    @Test
    public void aNonNumericReportIdIsRejected() throws Exception {
        mockMvc.perform(get("/api/v1/reports/not-a-number")).andExpect(status().isBadRequest());
    }

    @Test
    public void aNegativeReportIdIsNotFound() throws Exception {
        mockMvc.perform(get("/api/v1/reports/-1")).andExpect(status().isNotFound());
    }

    @Test
    public void aZeroReportIdIsNotFound() throws Exception {
        mockMvc.perform(get("/api/v1/reports/0")).andExpect(status().isNotFound());
    }

    @Test
    public void theLargestLongIdIsNotFound() throws Exception {
        mockMvc.perform(get("/api/v1/reports/" + Long.MAX_VALUE)).andExpect(status().isNotFound());
    }

    @Test
    public void anIdBeyondLongRangeIsRejected() throws Exception {
        mockMvc.perform(get("/api/v1/reports/9223372036854775808")).andExpect(status().isBadRequest());
    }

    @Test
    public void downloadingAnUnknownReportIsNotFound() throws Exception {
        mockMvc.perform(get("/api/v1/reports/" + Long.MAX_VALUE + "/download")).andExpect(status().isNotFound());
    }

    @Test
    public void deletingAnUnknownReportIsNotFound() throws Exception {
        mockMvc.perform(delete("/api/v1/reports/" + Long.MAX_VALUE)).andExpect(status().isNotFound());
    }

    @Test
    public void deletingTheSameReportTwiceIsNotFoundTheSecondTime() throws Exception {
        Long id = createReport("Delete Twice", "boundary-user-delete-twice");

        mockMvc.perform(delete("/api/v1/reports/" + id)).andExpect(status().isNoContent());
        mockMvc.perform(delete("/api/v1/reports/" + id)).andExpect(status().isNotFound());
    }

    // ------------------------------------------------------------- list filter

    @Test
    public void anUnknownStatusFilterIsRejected() throws Exception {
        mockMvc.perform(get("/api/v1/reports").param("status", "NOT_A_STATUS"))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void anEmptyUserIdFilterMatchesNobody() throws Exception {
        mockMvc.perform(get("/api/v1/reports").param("userId", ""))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total", is(0)));
    }

    @Test
    public void theUserFilterTakesPrecedenceOverTheStatusFilter() throws Exception {
        createReport("Precedence", "boundary-user-precedence");

        mockMvc.perform(get("/api/v1/reports")
                        .param("userId", "boundary-user-precedence")
                        .param("status", "FAILED"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total", is(1)))
                .andExpect(jsonPath("$.reports[0].requestedBy", is("boundary-user-precedence")));
    }

    // -------------------------------------------------------------- cross-user

    @Test
    public void oneUsersListingNeverContainsAnotherUsersReport() throws Exception {
        Long ownedByA = createReport("Owned By A", "boundary-user-a");
        createReport("Owned By B", "boundary-user-b");

        MvcResult listForB = mockMvc.perform(get("/api/v1/reports").param("userId", "boundary-user-b"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total", is(1)))
                .andExpect(jsonPath("$.reports[0].requestedBy", is("boundary-user-b")))
                .andReturn();

        org.junit.Assert.assertFalse(
                "user B's listing leaked user A's report id",
                listForB.getResponse().getContentAsString().contains("\"id\":" + ownedByA + ","));
    }

    @Test
    public void anyCallerCanCurrentlyReadAnotherUsersReport() throws Exception {
        // FINDING (documented, not fixed here): the service has no authentication or
        // ownership check. SecurityConfig permits /api/v1/reports/** ("TODO: Add JWT
        // validation") and ReportController never compares the caller to requestedBy,
        // so any caller can read, download or delete any user's report by id.
        Long ownedByA = createReport("Private To A", "boundary-user-authz-a");

        mockMvc.perform(get("/api/v1/reports/" + ownedByA))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.requestedBy", is("boundary-user-authz-a")));
    }

    @Test
    @Ignore("FINDING: report-service has no authz — reading another user's report by id should be 403/404")
    public void readingAnotherUsersReportShouldBeForbidden() throws Exception {
        Long ownedByA = createReport("Private To A 2", "boundary-user-authz-a2");

        mockMvc.perform(get("/api/v1/reports/" + ownedByA).header("X-User-ID", "boundary-user-authz-b2"))
                .andExpect(status().isForbidden());
    }

    @Test
    public void anyCallerCanCurrentlyDeleteAnotherUsersReport() throws Exception {
        // Same finding as above, on the destructive path.
        Long ownedByA = createReport("Deletable By Anyone", "boundary-user-authz-del-a");

        mockMvc.perform(delete("/api/v1/reports/" + ownedByA).header("X-User-ID", "boundary-user-authz-del-b"))
                .andExpect(status().isNoContent());
    }

    @Test
    @Ignore("FINDING: report-service has no authz — deleting another user's report should be 403")
    public void deletingAnotherUsersReportShouldBeForbidden() throws Exception {
        Long ownedByA = createReport("Not Deletable", "boundary-user-authz-del-a2");

        mockMvc.perform(delete("/api/v1/reports/" + ownedByA).header("X-User-ID", "boundary-user-authz-del-b2"))
                .andExpect(status().isForbidden());
    }

    @Test
    public void aUserIdDifferingOnlyByCaseIsADifferentOwner() throws Exception {
        createReport("Case Sensitive", "boundary-user-case");

        mockMvc.perform(get("/api/v1/reports").param("userId", "BOUNDARY-USER-CASE"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total", is(0)));
    }

    // ------------------------------------------------------------ configuration

    @Test
    public void theConfiguredRowCapIsFiftyThousand() throws Exception {
        org.junit.Assert.assertEquals(50000, appConfig.getMaxRows());
    }

    // ----------------------------------------------------------------- helpers

    private Long createReport(String name, String requestedBy) throws Exception {
        ReportRequest request = buildRequest(name, ReportCategory.USAGE_ANALYTICS, ReportType.CSV, requestedBy);

        MvcResult result = mockMvc.perform(post("/api/v1/reports")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isAccepted())
                .andReturn();

        @SuppressWarnings("unchecked")
        Map<String, Object> body = objectMapper.readValue(result.getResponse().getContentAsString(), Map.class);
        return Long.valueOf(String.valueOf(body.get("id")));
    }

    private ReportRequest buildRequest(String name, ReportCategory category, ReportType type, String requestedBy) {
        ReportRequest request = new ReportRequest();
        request.setReportName(name);
        request.setCategory(category);
        request.setReportType(type);
        request.setRequestedBy(requestedBy);
        request.setParameters(new HashMap<String, String>());
        return request;
    }
}
