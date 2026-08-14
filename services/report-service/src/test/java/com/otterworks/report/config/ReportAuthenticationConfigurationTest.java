package com.otterworks.report.config;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.junit4.SpringRunner;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@RunWith(SpringRunner.class)
@SpringBootTest(properties = "otterworks.auth.jwt-secret=")
@AutoConfigureMockMvc
@ActiveProfiles("test")
public class ReportAuthenticationConfigurationTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    public void missingJwtSecretDisablesReportEndpointsEvenWithUserHeader() throws Exception {
        mockMvc.perform(get("/api/v1/reports")
                        .header("X-User-ID", "header-only-user"))
                .andExpect(status().isUnauthorized());
    }
}
