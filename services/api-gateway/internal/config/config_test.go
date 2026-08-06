package config

import (
	"os"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// allEnvKeys lists every environment variable Load reads. Tests clear them all so
// a value leaking in from the ambient environment cannot change an expectation.
var allEnvKeys = []string{
	"PORT",
	"LOG_LEVEL",
	"AUTH_SERVICE_URL",
	"FILE_SERVICE_URL",
	"DOCUMENT_SERVICE_URL",
	"COLLAB_SERVICE_URL",
	"NOTIFICATION_SERVICE_URL",
	"SEARCH_SERVICE_URL",
	"ANALYTICS_SERVICE_URL",
	"ADMIN_SERVICE_URL",
	"AUDIT_SERVICE_URL",
	"REPORT_SERVICE_URL",
	"RATE_LIMIT_RPS",
	"JWT_SECRET",
	"CORS_ALLOWED_ORIGINS",
	"CORS_ALLOWED_METHODS",
	"CORS_ALLOWED_HEADERS",
	"CORS_MAX_AGE",
	"SHUTDOWN_TIMEOUT_SECONDS",
	"CB_MAX_REQUESTS",
	"CB_INTERVAL_SECONDS",
	"CB_TIMEOUT_SECONDS",
	"CB_FAILURE_RATIO",
}

// clearEnv removes every configuration variable for the duration of the test and
// restores the previous values afterwards, so tests cannot influence one another.
func clearEnv(t *testing.T) {
	t.Helper()
	for _, key := range allEnvKeys {
		if previous, ok := os.LookupEnv(key); ok {
			t.Cleanup(func() { os.Setenv(key, previous) })
		} else {
			t.Cleanup(func() { os.Unsetenv(key) })
		}
		require.NoError(t, os.Unsetenv(key))
	}
}

func TestLoad_DefaultsWhenEnvironmentIsEmpty(t *testing.T) {
	clearEnv(t)

	cfg := Load()

	assert.Equal(t, "8080", cfg.Port)
	assert.Equal(t, "info", cfg.LogLevel)
	assert.Equal(t, "http://auth-service:8081", cfg.AuthServiceURL)
	assert.Equal(t, "http://file-service:8082", cfg.FileServiceURL)
	assert.Equal(t, "http://document-service:8083", cfg.DocumentServiceURL)
	assert.Equal(t, "http://collab-service:8084", cfg.CollabServiceURL)
	assert.Equal(t, "http://notification-service:8086", cfg.NotificationServiceURL)
	assert.Equal(t, "http://search-service:8087", cfg.SearchServiceURL)
	assert.Equal(t, "http://analytics-service:8088", cfg.AnalyticsServiceURL)
	assert.Equal(t, "http://admin-service:8089", cfg.AdminServiceURL)
	assert.Equal(t, "http://audit-service:8090", cfg.AuditServiceURL)
	assert.Equal(t, "http://report-service:8091", cfg.ReportServiceURL)
	assert.Equal(t, 100, cfg.RateLimitRPS)
	assert.Equal(t, "", cfg.JWTSecret)
	assert.Equal(t, []string{"http://localhost:3000", "http://localhost:4200", "https://localhost", "capacitor://localhost"}, cfg.CORSAllowedOrigins)
	assert.Equal(t, []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}, cfg.CORSAllowedMethods)
	assert.Equal(t, []string{"Accept", "Authorization", "Content-Type", "X-Request-ID"}, cfg.CORSAllowedHeaders)
	assert.Equal(t, 300, cfg.CORSMaxAge)
	assert.Equal(t, 30*time.Second, cfg.ShutdownTimeout)
	assert.Equal(t, uint32(5), cfg.CBMaxRequests)
	assert.Equal(t, 60*time.Second, cfg.CBInterval)
	assert.Equal(t, 30*time.Second, cfg.CBTimeout)
	assert.InDelta(t, 0.6, cfg.CBFailureRatio, 1e-9)
}

func TestLoad_EveryValueCanBeOverridden(t *testing.T) {
	clearEnv(t)

	overrides := map[string]string{
		"PORT":                     "9999",
		"LOG_LEVEL":                "debug",
		"AUTH_SERVICE_URL":         "http://auth.test:1",
		"FILE_SERVICE_URL":         "http://file.test:2",
		"DOCUMENT_SERVICE_URL":     "http://document.test:3",
		"COLLAB_SERVICE_URL":       "http://collab.test:4",
		"NOTIFICATION_SERVICE_URL": "http://notification.test:5",
		"SEARCH_SERVICE_URL":       "http://search.test:6",
		"ANALYTICS_SERVICE_URL":    "http://analytics.test:7",
		"ADMIN_SERVICE_URL":        "http://admin.test:8",
		"AUDIT_SERVICE_URL":        "http://audit.test:9",
		"REPORT_SERVICE_URL":       "http://report.test:10",
		"RATE_LIMIT_RPS":           "42",
		"JWT_SECRET":               "s3cret",
		"CORS_ALLOWED_ORIGINS":     "https://a.test,https://b.test",
		"CORS_ALLOWED_METHODS":     "GET,POST",
		"CORS_ALLOWED_HEADERS":     "Authorization",
		"CORS_MAX_AGE":             "60",
		"SHUTDOWN_TIMEOUT_SECONDS": "5",
		"CB_MAX_REQUESTS":          "7",
		"CB_INTERVAL_SECONDS":      "11",
		"CB_TIMEOUT_SECONDS":       "13",
		"CB_FAILURE_RATIO":         "0.25",
	}
	for key, value := range overrides {
		t.Setenv(key, value)
	}

	cfg := Load()

	assert.Equal(t, "9999", cfg.Port)
	assert.Equal(t, "debug", cfg.LogLevel)
	assert.Equal(t, "http://auth.test:1", cfg.AuthServiceURL)
	assert.Equal(t, "http://file.test:2", cfg.FileServiceURL)
	assert.Equal(t, "http://document.test:3", cfg.DocumentServiceURL)
	assert.Equal(t, "http://collab.test:4", cfg.CollabServiceURL)
	assert.Equal(t, "http://notification.test:5", cfg.NotificationServiceURL)
	assert.Equal(t, "http://search.test:6", cfg.SearchServiceURL)
	assert.Equal(t, "http://analytics.test:7", cfg.AnalyticsServiceURL)
	assert.Equal(t, "http://admin.test:8", cfg.AdminServiceURL)
	assert.Equal(t, "http://audit.test:9", cfg.AuditServiceURL)
	assert.Equal(t, "http://report.test:10", cfg.ReportServiceURL)
	assert.Equal(t, 42, cfg.RateLimitRPS)
	assert.Equal(t, "s3cret", cfg.JWTSecret)
	assert.Equal(t, []string{"https://a.test", "https://b.test"}, cfg.CORSAllowedOrigins)
	assert.Equal(t, []string{"GET", "POST"}, cfg.CORSAllowedMethods)
	assert.Equal(t, []string{"Authorization"}, cfg.CORSAllowedHeaders)
	assert.Equal(t, 60, cfg.CORSMaxAge)
	assert.Equal(t, 5*time.Second, cfg.ShutdownTimeout)
	assert.Equal(t, uint32(7), cfg.CBMaxRequests)
	assert.Equal(t, 11*time.Second, cfg.CBInterval)
	assert.Equal(t, 13*time.Second, cfg.CBTimeout)
	assert.InDelta(t, 0.25, cfg.CBFailureRatio, 1e-9)
}

func TestLoad_EmptyStringOverridesStringValuesButNotSlices(t *testing.T) {
	clearEnv(t)

	// getEnv honours an explicitly empty value; getEnvSlice treats it as "unset".
	t.Setenv("PORT", "")
	t.Setenv("AUTH_SERVICE_URL", "")
	t.Setenv("CORS_ALLOWED_ORIGINS", "")

	cfg := Load()

	assert.Equal(t, "", cfg.Port, "an empty PORT is taken literally, not defaulted")
	assert.Equal(t, "", cfg.AuthServiceURL)
	assert.Equal(t, []string{"http://localhost:3000", "http://localhost:4200", "https://localhost", "capacitor://localhost"},
		cfg.CORSAllowedOrigins, "an empty CORS list falls back to the defaults")
}

func TestLoad_IntegerParsingFallsBackOnInvalidInput(t *testing.T) {
	clearEnv(t)

	invalid := []struct {
		name  string
		value string
	}{
		{"not a number", "abc"},
		{"empty", ""},
		{"whitespace", " 100 "},
		{"float", "12.5"},
		{"trailing junk", "100rps"},
		{"hex", "0x64"},
		{"plus separated", "1_000"},
		{"int64 overflow", "9223372036854775808"},
	}

	for _, tc := range invalid {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("RATE_LIMIT_RPS", tc.value)
			assert.Equal(t, 100, Load().RateLimitRPS,
				"invalid RATE_LIMIT_RPS %q must fall back to the default, not to zero", tc.value)
		})
	}
}

func TestLoad_RateLimitRPSBoundaryTrio(t *testing.T) {
	clearEnv(t)

	// Boundary trio around the 100 rps default.
	for _, tc := range []struct {
		value string
		want  int
	}{
		{"99", 99},
		{"100", 100},
		{"101", 101},
	} {
		t.Run(tc.value, func(t *testing.T) {
			t.Setenv("RATE_LIMIT_RPS", tc.value)
			assert.Equal(t, tc.want, Load().RateLimitRPS)
		})
	}
}

func TestLoad_NonPositiveNumericValuesAreAcceptedUnvalidated(t *testing.T) {
	clearEnv(t)

	// Load performs no range validation; these values are pinned as current behaviour.
	// A gateway started with RATE_LIMIT_RPS=0 rejects every request, and
	// SHUTDOWN_TIMEOUT_SECONDS=0 gives in-flight requests no grace period at all.
	t.Setenv("RATE_LIMIT_RPS", "0")
	t.Setenv("CORS_MAX_AGE", "-1")
	t.Setenv("SHUTDOWN_TIMEOUT_SECONDS", "0")
	t.Setenv("CB_INTERVAL_SECONDS", "-5")

	cfg := Load()

	assert.Equal(t, 0, cfg.RateLimitRPS)
	assert.Equal(t, -1, cfg.CORSMaxAge)
	assert.Equal(t, time.Duration(0), cfg.ShutdownTimeout)
	assert.Equal(t, -5*time.Second, cfg.CBInterval)
}

func TestLoad_NegativeCBMaxRequestsWrapsAroundUint32(t *testing.T) {
	clearEnv(t)

	// CB_MAX_REQUESTS is parsed as a signed int and converted with uint32(), so a
	// negative value silently becomes an enormous half-open request allowance —
	// effectively disabling the half-open probe limit. Pinned, not fixed.
	t.Setenv("CB_MAX_REQUESTS", "-1")

	assert.Equal(t, uint32(4294967295), Load().CBMaxRequests)
}

func TestLoad_FailureRatioParsing(t *testing.T) {
	clearEnv(t)

	cases := []struct {
		name  string
		value string
		want  float64
	}{
		{"invalid falls back", "not-a-ratio", 0.6},
		{"empty falls back", "", 0.6},
		{"below range accepted", "-0.5", -0.5},
		{"zero accepted", "0", 0},
		{"just below default", "0.59", 0.59},
		{"at default", "0.6", 0.6},
		{"just above default", "0.61", 0.61},
		{"one accepted", "1", 1},
		{"above one accepted", "1.5", 1.5},
		{"scientific notation", "6e-1", 0.6},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("CB_FAILURE_RATIO", tc.value)
			assert.InDelta(t, tc.want, Load().CBFailureRatio, 1e-9)
		})
	}
}

func TestLoad_SliceParsingDoesNotTrimOrDropEmptyEntries(t *testing.T) {
	clearEnv(t)

	// strings.Split is used verbatim: surrounding spaces and empty segments survive.
	// A CORS list written with spaces after the commas therefore never matches an Origin.
	t.Setenv("CORS_ALLOWED_ORIGINS", "https://a.test, https://b.test,,")

	assert.Equal(t,
		[]string{"https://a.test", " https://b.test", "", ""},
		Load().CORSAllowedOrigins)
}

func TestValidate_RequiresJWTSecret(t *testing.T) {
	cases := []struct {
		name      string
		secret    string
		wantError bool
	}{
		{"missing secret is rejected", "", true},
		{"single character secret is accepted", "x", false},
		{"whitespace-only secret is accepted", "   ", false},
		{"normal secret is accepted", "a-real-secret", false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := &Config{JWTSecret: tc.secret}
			err := cfg.Validate()
			if tc.wantError {
				require.Error(t, err)
				assert.Contains(t, err.Error(), "JWT_SECRET")
				return
			}
			assert.NoError(t, err)
		})
	}
}

func TestValidate_OnLoadedConfigWithoutJWTSecret(t *testing.T) {
	clearEnv(t)

	require.Error(t, Load().Validate(), "the gateway must refuse to start without JWT_SECRET")

	t.Setenv("JWT_SECRET", "present")
	assert.NoError(t, Load().Validate())
}

func TestServiceRoutes_MapsEveryPrefixToItsService(t *testing.T) {
	cfg := &Config{
		AuthServiceURL:         "http://auth",
		FileServiceURL:         "http://file",
		DocumentServiceURL:     "http://document",
		CollabServiceURL:       "http://collab",
		NotificationServiceURL: "http://notification",
		SearchServiceURL:       "http://search",
		AnalyticsServiceURL:    "http://analytics",
		AdminServiceURL:        "http://admin",
		AuditServiceURL:        "http://audit",
		ReportServiceURL:       "http://report",
	}

	assert.Equal(t, map[string]string{
		"/api/v1/auth":          "http://auth",
		"/api/v1/files":         "http://file",
		"/api/v1/folders":       "http://file",
		"/api/v1/documents":     "http://document",
		"/api/v1/templates":     "http://document",
		"/api/v1/collab":        "http://collab",
		"/socket.io":            "http://collab",
		"/api/v1/notifications": "http://notification",
		"/api/v1/preferences":   "http://notification",
		"/api/v1/search":        "http://search",
		"/api/v1/analytics":     "http://analytics",
		"/api/v1/admin":         "http://admin",
		"/api/v1/audit":         "http://audit",
		"/api/v1/reports":       "http://report",
		"/api/v1/settings":      "http://auth",
	}, cfg.ServiceRoutes())
}

func TestServiceRoutes_PrefixesAreWellFormed(t *testing.T) {
	for prefix, target := range Load().ServiceRoutes() {
		assert.True(t, strings.HasPrefix(prefix, "/"), "prefix %q must be absolute", prefix)
		assert.False(t, strings.HasSuffix(prefix, "/"), "prefix %q must not end in a slash (chi would panic)", prefix)
		assert.NotEmpty(t, target, "prefix %q must have a target", prefix)
	}
}

// The four prefixes below are listed in docs/api-route-matrix.md under "Known route and
// behavior gaps" as *not* being in ServiceRoutes. They are in fact routed today: the gaps
// were closed and the document was not updated (see PR description). These assertions pin
// the routing that exists now, so removing any of these prefixes again turns the test red.
func TestServiceRoutes_RouteMatrixGapPrefixesAreRoutedToday_seeRouteMatrix(t *testing.T) {
	routes := Load().ServiceRoutes()

	cases := []struct {
		prefix  string
		service string
	}{
		{"/api/v1/templates", "http://document-service:8083"},
		{"/api/v1/folders", "http://file-service:8082"},
		{"/api/v1/reports", "http://report-service:8091"},
		{"/api/v1/preferences", "http://notification-service:8086"},
	}

	for _, tc := range cases {
		t.Run(tc.prefix, func(t *testing.T) {
			target, ok := routes[tc.prefix]
			require.True(t, ok, "%s is documented as an unrouted gap but should be routed", tc.prefix)
			assert.Equal(t, tc.service, target)
		})
	}
}

func TestServiceRoutes_UsesLoadedServiceURLs(t *testing.T) {
	clearEnv(t)
	t.Setenv("FILE_SERVICE_URL", "http://file.override:1234")

	routes := Load().ServiceRoutes()

	assert.Equal(t, "http://file.override:1234", routes["/api/v1/files"])
	assert.Equal(t, "http://file.override:1234", routes["/api/v1/folders"],
		"/api/v1/folders must follow the same service URL as /api/v1/files")
}

func TestServiceRoutes_DoesNotRouteUnknownPrefixes(t *testing.T) {
	routes := Load().ServiceRoutes()

	for _, prefix := range []string{
		"",
		"/",
		"/health",
		"/metrics",
		"/api/v1",
		"/api/v2/files",
		"/api/v1/Files",
		"/api/v1/files/",
		"/api/v1/unknown",
	} {
		_, ok := routes[prefix]
		assert.False(t, ok, "%q must not be a gateway route prefix", prefix)
	}
}
