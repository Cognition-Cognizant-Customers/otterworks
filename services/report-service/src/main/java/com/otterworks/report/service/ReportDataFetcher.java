package com.otterworks.report.service;

import com.google.common.cache.CacheBuilder;
import com.google.common.cache.CacheLoader;
import com.google.common.cache.LoadingCache;
import com.otterworks.report.config.AppConfig;
import com.otterworks.report.util.ReportDateUtils;
import org.apache.commons.lang3.StringUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;

/**
 * Fetches report data from analytics and audit services via REST.
 *
 * LEGACY PATTERNS:
 * - RestTemplate instead of WebClient (reactive) or RestClient (Spring 6.1+)
 * - Guava LoadingCache instead of Caffeine or Spring Cache
 * - Manual JSON response handling with Map<String, Object>
 * - java.util.Date parameters
 * - Checked exceptions wrapped in RuntimeException
 */
@Service
public class ReportDataFetcher {

    private static final Logger logger = LoggerFactory.getLogger(ReportDataFetcher.class);

    private final RestTemplate restTemplate;
    private final AppConfig appConfig;

    // Guava LoadingCache; consider Caffeine (Spring Boot default) or Spring @Cacheable
    private final LoadingCache<String, List<Map<String, Object>>> dataCache;

    public ReportDataFetcher(RestTemplate restTemplate, AppConfig appConfig) {
        this.restTemplate = restTemplate;
        this.appConfig = appConfig;

        this.dataCache = CacheBuilder.newBuilder()
                .maximumSize(100)
                .expireAfterWrite(5, TimeUnit.MINUTES)
                .build(new CacheLoader<String, List<Map<String, Object>>>() {
                    @Override
                    public List<Map<String, Object>> load(String key) throws Exception {
                        // Cache loader delegates to the appropriate fetch method
                        return Collections.emptyList();
                    }
                });
    }

    /**
     * Fetch analytics data for a date range.
     *
     * LEGACY: Uses RestTemplate.getForEntity with manual URL construction.
     * Modern approach: WebClient with URI builder and reactive types.
     */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> fetchAnalyticsData(Date dateFrom, Date dateTo, Map<String, String> parameters) {
        String metric = (parameters != null) ? parameters.get("metric") : null;
        String cacheKey = "analytics:" + ReportDateUtils.toIsoString(dateFrom) + ":" + ReportDateUtils.toIsoString(dateTo)
                + (metric != null ? ":metric=" + metric : "");

        try {
            return dataCache.get(cacheKey, () -> {
                String url = appConfig.getAnalyticsServiceUrl() + "/api/v1/analytics/events"
                        + "?from=" + ReportDateUtils.toIsoString(dateFrom)
                        + "&to=" + ReportDateUtils.toIsoString(dateTo);

                if (parameters != null && StringUtils.isNotBlank(parameters.get("metric"))) {
                    url += "&metric=" + parameters.get("metric");
                }

                logger.info("Fetching analytics data from: {}", url);

                // Let RestClientException propagate so fallback data is NOT cached.
                // Only successful responses are stored in the Guava cache.
                ResponseEntity<Map> response = restTemplate.getForEntity(url, Map.class);
                if (response.getBody() != null && response.getBody().containsKey("events")) {
                    return (List<Map<String, Object>>) response.getBody().get("events");
                }
                return Collections.emptyList();
            });
        } catch (ExecutionException e) {
            logger.error("Failed to fetch analytics data, using sample data: {}", e.getMessage());
            return generateSampleAnalyticsData(dateFrom, dateTo);
        }
    }

    /**
     * Fetch audit log entries for a date range.
     */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> fetchAuditData(Date dateFrom, Date dateTo, Map<String, String> parameters) {
        String cacheKey = "audit:" + ReportDateUtils.toIsoString(dateFrom) + ":" + ReportDateUtils.toIsoString(dateTo);

        try {
            return dataCache.get(cacheKey, () -> {
                String url = appConfig.getAuditServiceUrl() + "/api/v1/audit/events"
                        + "?from=" + ReportDateUtils.toIsoString(dateFrom)
                        + "&to=" + ReportDateUtils.toIsoString(dateTo);

                logger.info("Fetching audit data from: {}", url);

                // Let RestClientException propagate so fallback data is NOT cached.
                ResponseEntity<Map> response = restTemplate.getForEntity(url, Map.class);
                if (response.getBody() != null && response.getBody().containsKey("events")) {
                    return (List<Map<String, Object>>) response.getBody().get("events");
                }
                return Collections.emptyList();
            });
        } catch (ExecutionException e) {
            logger.error("Failed to fetch audit data, using sample data: {}", e.getMessage());
            return generateSampleAuditData(dateFrom, dateTo);
        }
    }

    /**
     * Fetch user activity data.
     */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> fetchUserActivityData(Date dateFrom, Date dateTo, Map<String, String> parameters) {
        String url = appConfig.getAuthServiceUrl() + "/api/v1/users/activity"
                + "?from=" + ReportDateUtils.toIsoString(dateFrom)
                + "&to=" + ReportDateUtils.toIsoString(dateTo);

        logger.info("Fetching user activity from: {}", url);

        try {
            ResponseEntity<Map> response = restTemplate.getForEntity(url, Map.class);
            if (response.getBody() != null && response.getBody().containsKey("activities")) {
                return (List<Map<String, Object>>) response.getBody().get("activities");
            }
            return Collections.emptyList();
        } catch (RestClientException e) {
            logger.error("Failed to fetch user activity data: {}", e.getMessage());
            return generateSampleUserActivityData(dateFrom, dateTo);
        }
    }

    // ----- Sample data generators for standalone/demo mode -----

    private List<Map<String, Object>> generateSampleAnalyticsData(Date dateFrom, Date dateTo) {
        List<Map<String, Object>> data = new ArrayList<>();
        String[] events = {"file_upload", "file_download", "doc_create", "doc_edit", "doc_share", "search_query"};
        String[] users = {"user-001", "user-002", "user-003", "user-004", "user-005"};

        for (int i = 0; i < 50; i++) {
            Map<String, Object> row = new HashMap<>();
            row.put("event_id", "evt-" + String.format("%04d", i));
            row.put("event_type", events[i % events.length]);
            row.put("user_id", users[i % users.length]);
            row.put("timestamp", ReportDateUtils.toIsoString(new Date(dateFrom.getTime() + (long) i * 3600000)));
            row.put("duration_ms", 100 + (i * 17) % 5000);
            row.put("status", i % 10 == 0 ? "error" : "success");
            row.put("metadata", "sample-analytics-row-" + i);
            data.add(row);
        }
        return data;
    }

    private List<Map<String, Object>> generateSampleAuditData(Date dateFrom, Date dateTo) {
        List<Map<String, Object>> data = new ArrayList<>();
        String[] actions = {"LOGIN", "LOGOUT", "FILE_ACCESS", "PERMISSION_CHANGE", "ADMIN_ACTION", "API_CALL"};
        String[] results = {"SUCCESS", "FAILURE", "DENIED"};

        for (int i = 0; i < 50; i++) {
            Map<String, Object> row = new HashMap<>();
            row.put("audit_id", "aud-" + String.format("%04d", i));
            row.put("action", actions[i % actions.length]);
            row.put("actor", "user-" + String.format("%03d", i % 10));
            row.put("result", results[i % results.length]);
            row.put("ip_address", "192.168.1." + (i % 255));
            row.put("timestamp", ReportDateUtils.toIsoString(new Date(dateFrom.getTime() + (long) i * 1800000)));
            row.put("resource", "/files/doc-" + (i % 20));
            row.put("details", "Audit entry " + i);
            data.add(row);
        }
        return data;
    }

    private List<Map<String, Object>> generateSampleUserActivityData(Date dateFrom, Date dateTo) {
        List<Map<String, Object>> data = new ArrayList<>();

        for (int i = 0; i < 25; i++) {
            Map<String, Object> row = new HashMap<>();
            row.put("user_id", "user-" + String.format("%03d", i));
            row.put("email", "user" + i + "@otterworks.example.com");
            row.put("last_login", ReportDateUtils.toIsoString(ReportDateUtils.daysAgo(i % 7)));
            row.put("files_uploaded", 10 + i * 3);
            row.put("docs_created", 5 + i * 2);
            row.put("storage_used_mb", 100 + i * 50);
            row.put("collaborations", i * 4);
            row.put("active", i % 5 != 0);
            data.add(row);
        }
        return data;
    }
}
