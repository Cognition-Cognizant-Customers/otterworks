package com.otterworks.report.config;

import org.apache.hc.client5.http.config.ConnectionConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManager;
import org.apache.hc.core5.http.io.SocketConfig;
import org.apache.hc.core5.util.Timeout;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

/**
 * Application configuration — wires up RestTemplate and external service URLs.
 *
 * Uses RestTemplate backed by an Apache HttpClient 5 connection pool.
 */
@Configuration
public class AppConfig {

    @Value("${otterworks.analytics-service.url:http://analytics-service:8088}")
    private String analyticsServiceUrl;

    @Value("${otterworks.audit-service.url:http://audit-service:8090}")
    private String auditServiceUrl;

    @Value("${otterworks.auth-service.url:http://auth-service:8081}")
    private String authServiceUrl;

    @Value("${otterworks.report.output-dir:/tmp/reports}")
    private String reportOutputDir;

    @Value("${otterworks.report.max-rows:50000}")
    private int maxRows;

    @Value("${otterworks.report.connection-timeout:5000}")
    private int connectionTimeout;

    @Value("${otterworks.report.read-timeout:30000}")
    private int readTimeout;

    @Bean
    public RestTemplate restTemplate() {
        PoolingHttpClientConnectionManager connectionManager = new PoolingHttpClientConnectionManager();
        connectionManager.setMaxTotal(50);
        connectionManager.setDefaultMaxPerRoute(20);
        connectionManager.setDefaultConnectionConfig(ConnectionConfig.custom()
                .setConnectTimeout(Timeout.ofMilliseconds(connectionTimeout))
                .build());
        connectionManager.setDefaultSocketConfig(SocketConfig.custom()
                .setSoTimeout(Timeout.ofMilliseconds(readTimeout))
                .build());

        CloseableHttpClient httpClient = HttpClients.custom()
                .setConnectionManager(connectionManager)
                .build();

        HttpComponentsClientHttpRequestFactory factory = new HttpComponentsClientHttpRequestFactory(httpClient);

        return new RestTemplate(factory);
    }

    public String getAnalyticsServiceUrl() {
        return analyticsServiceUrl;
    }

    public String getAuditServiceUrl() {
        return auditServiceUrl;
    }

    public String getAuthServiceUrl() {
        return authServiceUrl;
    }

    public String getReportOutputDir() {
        return reportOutputDir;
    }

    public int getMaxRows() {
        return maxRows;
    }
}
