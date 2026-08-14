package com.otterworks.report;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.report.model.ReportCategory;
import com.otterworks.report.model.ReportRequest;
import com.otterworks.report.model.ReportType;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.junit4.SpringRunner;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

import java.util.Date;

import static org.hamcrest.Matchers.anyOf;
import static org.hamcrest.Matchers.is;
import static org.hamcrest.Matchers.notNullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Integration tests for the Report Service.
 *
 * LEGACY PATTERNS:
 * - JUnit 4 with @RunWith(SpringRunner.class) instead of JUnit 5 @ExtendWith(SpringExtension.class)
 * - @Test from org.junit.Test instead of org.junit.jupiter.api.Test
 * - No @DisplayName or @Nested (JUnit 5 features)
 * - java.util.Date in test data
 * - Static imports from hamcrest (still valid but JUnit 5 prefers assertj)
 *
 * UPGRADE TARGET:
 * - Replace @RunWith(SpringRunner.class) with @ExtendWith(SpringExtension.class) or just @SpringBootTest
 * - Replace org.junit.Test with org.junit.jupiter.api.Test
 * - Replace Hamcrest matchers with AssertJ assertions
 * - Use @DisplayName for readable test names
 * - Use @Nested for test grouping
 */
@RunWith(SpringRunner.class)
@SpringBootTest(properties = "otterworks.auth.jwt-secret=" + TestAuth.TEST_SECRET)
@AutoConfigureMockMvc
@ActiveProfiles("test")
public class ReportServiceTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    public void healthEndpointShouldReturnOk() throws Exception {
        mockMvc.perform(get("/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status", is("healthy")))
                .andExpect(jsonPath("$.service", is("report-service")))
                .andExpect(jsonPath("$.version", is("0.1.0")));
    }

    @Test
    public void createReportShouldReturnAccepted() throws Exception {
        ReportRequest request = new ReportRequest();
        request.setReportName("Test Usage Report");
        request.setCategory(ReportCategory.USAGE_ANALYTICS);
        request.setReportType(ReportType.PDF);
        request.setRequestedBy("test-user-001");
        request.setDateFrom(new Date(System.currentTimeMillis() - 86400000L * 30)); // 30 days ago
        request.setDateTo(new Date());

        mockMvc.perform(withAuth(post("/api/v1/reports"), "test-user-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.id", notNullValue()))
                .andExpect(jsonPath("$.reportName", is("Test Usage Report")))
                .andExpect(jsonPath("$.category", is("USAGE_ANALYTICS")))
                .andExpect(jsonPath("$.reportType", is("PDF")))
                // @Async generation may complete before response is serialized
                .andExpect(jsonPath("$.status", anyOf(is("PENDING"), is("GENERATING"), is("COMPLETED"))));
    }

    @Test
    public void createCsvReportShouldReturnAccepted() throws Exception {
        ReportRequest request = new ReportRequest();
        request.setReportName("Audit Log Export");
        request.setCategory(ReportCategory.AUDIT_LOG);
        request.setReportType(ReportType.CSV);
        request.setRequestedBy("test-user-002");

        mockMvc.perform(withAuth(post("/api/v1/reports"), "test-user-002")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.reportType", is("CSV")));
    }

    @Test
    public void createExcelReportShouldReturnAccepted() throws Exception {
        ReportRequest request = new ReportRequest();
        request.setReportName("User Activity Summary");
        request.setCategory(ReportCategory.USER_ACTIVITY);
        request.setReportType(ReportType.EXCEL);
        request.setRequestedBy("test-user-003");

        mockMvc.perform(withAuth(post("/api/v1/reports"), "test-user-003")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.reportType", is("EXCEL")));
    }

    @Test
    public void getReportNotFoundShouldReturn404() throws Exception {
        mockMvc.perform(withAuth(get("/api/v1/reports/99999"), "test-user-001"))
                .andExpect(status().isNotFound());
    }

    @Test
    public void listReportsShouldReturnEmptyList() throws Exception {
        mockMvc.perform(withAuth(get("/api/v1/reports")
                        .param("userId", "nonexistent-user"), "nonexistent-user"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reports").isArray())
                .andExpect(jsonPath("$.total", is(0)));
    }

    @Test
    public void createReportWithoutNameShouldReturn400() throws Exception {
        ReportRequest request = new ReportRequest();
        request.setCategory(ReportCategory.AUDIT_LOG);
        request.setReportType(ReportType.PDF);
        request.setRequestedBy("test-user");
        // Missing reportName — should fail validation

        mockMvc.perform(withAuth(post("/api/v1/reports"), "test-user")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isBadRequest());
    }

    @Test
    public void downloadNonExistentReportShouldReturn404() throws Exception {
        mockMvc.perform(withAuth(get("/api/v1/reports/99999/download"), "test-user-001"))
                .andExpect(status().isNotFound());
    }

    private MockHttpServletRequestBuilder withAuth(
            MockHttpServletRequestBuilder builder, String userId) {
        return builder.header("Authorization", "Bearer " + tokenFor(userId));
    }

    private String tokenFor(String userId) {
        return TestAuth.tokenFor(userId);
    }
}
