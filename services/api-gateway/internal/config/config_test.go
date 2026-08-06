package config

import (
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// gatewayEnvKeys is every environment variable Load() reads.
var gatewayEnvKeys = []string{
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

// clearEnv unsets every gateway variable for the duration of the test and
// restores the previous process environment afterwards, so tests neither
// depend on the ambient environment nor on each other's ordering.
func clearEnv(t *testing.T) {
	t.Helper()
	for _, key := range gatewayEnvKeys {
		key := key
		if old, ok := os.LookupEnv(key); ok {
			t.Cleanup(func() { _ = os.Setenv(key, old) })
		} else {
			t.Cleanup(func() { _ = os.Unsetenv(key) })
		}
		require.NoError(t, os.Unsetenv(key))
	}
}

// loadWith clears the environment, applies the given overrides and loads.
func loadWith(t *testing.T, env map[string]string) *Config {
	t.Helper()
	clearEnv(t)
	for k, v := range env {
		t.Setenv(k, v)
	}
	return Load()
}

// --- defaults ---------------------------------------------------------------

func TestLoad_DefaultsWhenNothingIsSet(t *testing.T) {
	cfg := loadWith(t, nil)

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
	assert.Equal(t, "", cfg.JWTSecret, "there is deliberately no default JWT secret")

	assert.Equal(t,
		[]string{"http://localhost:3000", "http://localhost:4200", "https://localhost", "capacitor://localhost"},
		cfg.CORSAllowedOrigins)
	assert.Equal(t, []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}, cfg.CORSAllowedMethods)
	assert.Equal(t, []string{"Accept", "Authorization", "Content-Type", "X-Request-ID"}, cfg.CORSAllowedHeaders)
	assert.Equal(t, 300, cfg.CORSMaxAge)

	assert.Equal(t, 30*time.Second, cfg.ShutdownTimeout)

	assert.Equal(t, uint32(5), cfg.CBMaxRequests)
	assert.Equal(t, 60*time.Second, cfg.CBInterval)
	assert.Equal(t, 30*time.Second, cfg.CBTimeout)
	assert.InDelta(t, 0.6, cfg.CBFailureRatio, 1e-9)
}

// --- overrides --------------------------------------------------------------

func TestLoad_StringOverrides(t *testing.T) {
	cases := []struct {
		key   string
		value string
		get   func(*Config) string
	}{
		{"PORT", "9090", func(c *Config) string { return c.Port }},
		{"LOG_LEVEL", "debug", func(c *Config) string { return c.LogLevel }},
		{"AUTH_SERVICE_URL", "http://auth.test:1", func(c *Config) string { return c.AuthServiceURL }},
		{"FILE_SERVICE_URL", "http://file.test:2", func(c *Config) string { return c.FileServiceURL }},
		{"DOCUMENT_SERVICE_URL", "http://doc.test:3", func(c *Config) string { return c.DocumentServiceURL }},
		{"COLLAB_SERVICE_URL", "http://collab.test:4", func(c *Config) string { return c.CollabServiceURL }},
		{"NOTIFICATION_SERVICE_URL", "http://notify.test:5", func(c *Config) string { return c.NotificationServiceURL }},
		{"SEARCH_SERVICE_URL", "http://search.test:6", func(c *Config) string { return c.SearchServiceURL }},
		{"ANALYTICS_SERVICE_URL", "http://analytics.test:7", func(c *Config) string { return c.AnalyticsServiceURL }},
		{"ADMIN_SERVICE_URL", "http://admin.test:8", func(c *Config) string { return c.AdminServiceURL }},
		{"AUDIT_SERVICE_URL", "http://audit.test:9", func(c *Config) string { return c.AuditServiceURL }},
		{"REPORT_SERVICE_URL", "http://report.test:10", func(c *Config) string { return c.ReportServiceURL }},
		{"JWT_SECRET", "s3cret", func(c *Config) string { return c.JWTSecret }},
	}

	for _, tc := range cases {
		t.Run(tc.key, func(t *testing.T) {
			cfg := loadWith(t, map[string]string{tc.key: tc.value})
			assert.Equal(t, tc.value, tc.get(cfg))
		})
	}
}

// An explicitly empty string is a value, not an absence: getEnv returns it and
// the default is lost. Pinned because it is the difference between "unset" and
// "set to empty" in a Helm values file.
func TestLoad_EmptyStringOverrideWinsOverDefault(t *testing.T) {
	cfg := loadWith(t, map[string]string{"PORT": "", "LOG_LEVEL": ""})

	assert.Equal(t, "", cfg.Port)
	assert.Equal(t, "", cfg.LogLevel)
}

// getEnvSlice is the exception: an empty string falls back to the default.
func TestLoad_EmptyCORSListFallsBackToDefault(t *testing.T) {
	cfg := loadWith(t, map[string]string{
		"CORS_ALLOWED_ORIGINS": "",
		"CORS_ALLOWED_METHODS": "",
		"CORS_ALLOWED_HEADERS": "",
	})

	assert.Len(t, cfg.CORSAllowedOrigins, 4)
	assert.Equal(t, []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}, cfg.CORSAllowedMethods)
	assert.Len(t, cfg.CORSAllowedHeaders, 4)
}

func TestLoad_CORSListParsing(t *testing.T) {
	cases := []struct {
		name     string
		value    string
		expected []string
	}{
		{"single origin", "https://otterworks.app", []string{"https://otterworks.app"}},
		{"two origins", "https://a.test,https://b.test", []string{"https://a.test", "https://b.test"}},
		{"wildcard", "*", []string{"*"}},
		// No trimming is performed: surrounding spaces survive into the value.
		{"spaces are not trimmed", "https://a.test, https://b.test", []string{"https://a.test", " https://b.test"}},
		{"empty element is kept", "https://a.test,,https://b.test", []string{"https://a.test", "", "https://b.test"}},
		{"trailing comma yields empty element", "https://a.test,", []string{"https://a.test", ""}},
		{"lone comma yields two empties", ",", []string{"", ""}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"CORS_ALLOWED_ORIGINS": tc.value})
			assert.Equal(t, tc.expected, cfg.CORSAllowedOrigins)
		})
	}
}

// --- numeric boundaries (limit-1 / limit / limit+1 around each default) ------

func TestLoad_RateLimitRPSBoundaries(t *testing.T) {
	for _, v := range []int{99, 100, 101} {
		t.Run(strconv.Itoa(v), func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"RATE_LIMIT_RPS": strconv.Itoa(v)})
			assert.Equal(t, v, cfg.RateLimitRPS)
		})
	}
}

func TestLoad_CORSMaxAgeBoundaries(t *testing.T) {
	for _, v := range []int{299, 300, 301} {
		t.Run(strconv.Itoa(v), func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"CORS_MAX_AGE": strconv.Itoa(v)})
			assert.Equal(t, v, cfg.CORSMaxAge)
		})
	}
}

func TestLoad_ShutdownTimeoutBoundaries(t *testing.T) {
	for _, v := range []int{29, 30, 31} {
		t.Run(strconv.Itoa(v), func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"SHUTDOWN_TIMEOUT_SECONDS": strconv.Itoa(v)})
			assert.Equal(t, time.Duration(v)*time.Second, cfg.ShutdownTimeout)
		})
	}
}

func TestLoad_CircuitBreakerBoundaries(t *testing.T) {
	for _, v := range []int{4, 5, 6} {
		t.Run("CB_MAX_REQUESTS="+strconv.Itoa(v), func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"CB_MAX_REQUESTS": strconv.Itoa(v)})
			assert.Equal(t, uint32(v), cfg.CBMaxRequests)
		})
	}
	for _, v := range []int{59, 60, 61} {
		t.Run("CB_INTERVAL_SECONDS="+strconv.Itoa(v), func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"CB_INTERVAL_SECONDS": strconv.Itoa(v)})
			assert.Equal(t, time.Duration(v)*time.Second, cfg.CBInterval)
		})
	}
	for _, v := range []int{29, 30, 31} {
		t.Run("CB_TIMEOUT_SECONDS="+strconv.Itoa(v), func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"CB_TIMEOUT_SECONDS": strconv.Itoa(v)})
			assert.Equal(t, time.Duration(v)*time.Second, cfg.CBTimeout)
		})
	}
	for _, v := range []string{"0.59", "0.6", "0.61", "0", "1", "1.5", "-0.5"} {
		t.Run("CB_FAILURE_RATIO="+v, func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"CB_FAILURE_RATIO": v})
			expected, err := strconv.ParseFloat(v, 64)
			require.NoError(t, err)
			assert.InDelta(t, expected, cfg.CBFailureRatio, 1e-9)
		})
	}
}

// --- invalid values ---------------------------------------------------------

func TestLoad_InvalidIntFallsBackToDefault(t *testing.T) {
	invalid := []string{"", "abc", "1.5", "10x", " 10", "0x10", "1e3", "١٠"}

	for _, v := range invalid {
		t.Run("RATE_LIMIT_RPS="+v, func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"RATE_LIMIT_RPS": v})
			assert.Equal(t, 100, cfg.RateLimitRPS, "unparseable %q must fall back to the default", v)
		})
		t.Run("CORS_MAX_AGE="+v, func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"CORS_MAX_AGE": v})
			assert.Equal(t, 300, cfg.CORSMaxAge)
		})
		t.Run("SHUTDOWN_TIMEOUT_SECONDS="+v, func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"SHUTDOWN_TIMEOUT_SECONDS": v})
			assert.Equal(t, 30*time.Second, cfg.ShutdownTimeout)
		})
	}
}

func TestLoad_InvalidFloatFallsBackToDefault(t *testing.T) {
	for _, v := range []string{"", "abc", "0.6.1", "60%", "one"} {
		t.Run("CB_FAILURE_RATIO="+v, func(t *testing.T) {
			cfg := loadWith(t, map[string]string{"CB_FAILURE_RATIO": v})
			assert.InDelta(t, 0.6, cfg.CBFailureRatio, 1e-9)
		})
	}
}

// Load() performs no range validation: zero and negative values are accepted
// verbatim for every int setting. Pinned so that adding validation is a
// deliberate, test-visible change.
func TestLoad_ZeroAndNegativeIntsAreAcceptedUnvalidated(t *testing.T) {
	cfg := loadWith(t, map[string]string{
		"RATE_LIMIT_RPS":           "0",
		"CORS_MAX_AGE":             "-1",
		"SHUTDOWN_TIMEOUT_SECONDS": "0",
		"CB_INTERVAL_SECONDS":      "-60",
	})

	assert.Equal(t, 0, cfg.RateLimitRPS, "a rate limit of 0 is accepted (no validation)")
	assert.Equal(t, -1, cfg.CORSMaxAge)
	assert.Equal(t, time.Duration(0), cfg.ShutdownTimeout)
	assert.Equal(t, -60*time.Second, cfg.CBInterval)
}

// FINDING (documents current behavior, no fix here): CB_MAX_REQUESTS is read as
// an int and converted with uint32(), so out-of-range input wraps silently
// instead of being rejected. "-1" becomes 4294967295 (effectively unlimited
// half-open probes) and 2^32 becomes 0.
func TestLoad_CBMaxRequestsWrapsOnOutOfRangeInput_FINDING(t *testing.T) {
	cfg := loadWith(t, map[string]string{"CB_MAX_REQUESTS": "-1"})
	assert.Equal(t, uint32(4294967295), cfg.CBMaxRequests,
		"current behavior: a negative value wraps to the uint32 maximum")

	cfg = loadWith(t, map[string]string{"CB_MAX_REQUESTS": "4294967296"})
	assert.Equal(t, uint32(0), cfg.CBMaxRequests,
		"current behavior: 2^32 truncates to 0")
}

func TestLoad_CBMaxRequestsShouldRejectOutOfRangeInput(t *testing.T) {
	t.Skip("DEFECT: uint32(getEnvInt(...)) in Load() silently wraps out-of-range CB_MAX_REQUESTS " +
		"instead of falling back to the default. Fixing it is a production change (validate the " +
		"range in config.go), out of scope for this coverage PR.")

	cfg := loadWith(t, map[string]string{"CB_MAX_REQUESTS": "-1"})
	assert.Equal(t, uint32(5), cfg.CBMaxRequests)
}

// --- Validate ---------------------------------------------------------------

func TestValidate(t *testing.T) {
	cases := []struct {
		name      string
		secret    string
		expectErr bool
	}{
		{"empty secret is rejected", "", true},
		{"single character secret is accepted", "x", false},
		{"whitespace-only secret is accepted", " ", false},
		{"normal secret is accepted", "a-long-enough-secret", false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := &Config{JWTSecret: tc.secret}
			err := cfg.Validate()
			if tc.expectErr {
				require.Error(t, err)
				assert.Contains(t, err.Error(), "JWT_SECRET")
				return
			}
			assert.NoError(t, err)
		})
	}
}

// Validate only checks presence: there is no minimum length or entropy rule,
// so a one-character secret passes. Pinned deliberately.
func TestValidate_OnlyChecksPresenceNotStrength(t *testing.T) {
	cfg := loadWith(t, map[string]string{"JWT_SECRET": "x"})
	assert.NoError(t, cfg.Validate())

	cfg = loadWith(t, nil)
	require.Error(t, cfg.Validate(), "a gateway loaded with no JWT_SECRET must not validate")
}

// --- ServiceRoutes ----------------------------------------------------------

func TestServiceRoutes_MapsEveryPrefixToItsService(t *testing.T) {
	cfg := loadWith(t, map[string]string{
		"AUTH_SERVICE_URL":         "http://auth.test",
		"FILE_SERVICE_URL":         "http://file.test",
		"DOCUMENT_SERVICE_URL":     "http://doc.test",
		"COLLAB_SERVICE_URL":       "http://collab.test",
		"NOTIFICATION_SERVICE_URL": "http://notify.test",
		"SEARCH_SERVICE_URL":       "http://search.test",
		"ANALYTICS_SERVICE_URL":    "http://analytics.test",
		"ADMIN_SERVICE_URL":        "http://admin.test",
		"AUDIT_SERVICE_URL":        "http://audit.test",
		"REPORT_SERVICE_URL":       "http://report.test",
	})

	expected := map[string]string{
		"/api/v1/auth":          "http://auth.test",
		"/api/v1/files":         "http://file.test",
		"/api/v1/folders":       "http://file.test",
		"/api/v1/documents":     "http://doc.test",
		"/api/v1/templates":     "http://doc.test",
		"/api/v1/collab":        "http://collab.test",
		"/socket.io":            "http://collab.test",
		"/api/v1/notifications": "http://notify.test",
		"/api/v1/preferences":   "http://notify.test",
		"/api/v1/search":        "http://search.test",
		"/api/v1/analytics":     "http://analytics.test",
		"/api/v1/admin":         "http://admin.test",
		"/api/v1/audit":         "http://audit.test",
		"/api/v1/reports":       "http://report.test",
		"/api/v1/settings":      "http://auth.test",
	}

	assert.Equal(t, expected, cfg.ServiceRoutes())
}

// The four prefixes docs/api-route-matrix.md lists as gateway gaps are all
// present today. See internal/proxy/router_test.go for the routed-behavior
// counterpart of these assertions.
func TestServiceRoutes_DocumentedRouteGapsAreClosed(t *testing.T) {
	cfg := loadWith(t, nil)
	routes := cfg.ServiceRoutes()

	gaps := map[string]string{
		"/api/v1/templates":   cfg.DocumentServiceURL,
		"/api/v1/folders":     cfg.FileServiceURL,
		"/api/v1/reports":     cfg.ReportServiceURL,
		"/api/v1/preferences": cfg.NotificationServiceURL,
	}

	for prefix, target := range gaps {
		t.Run(prefix, func(t *testing.T) {
			got, ok := routes[prefix]
			require.True(t, ok, "%s is missing from ServiceRoutes", prefix)
			assert.Equal(t, target, got)
		})
	}
}

func TestServiceRoutes_NoPrefixHasATrailingSlash(t *testing.T) {
	// chi's Route() panics on a pattern with a trailing slash, so this is a
	// structural invariant of the routing table.
	for prefix := range loadWith(t, nil).ServiceRoutes() {
		assert.Equal(t, "/", string(prefix[0]), "prefix %q must start with /", prefix)
		assert.NotEqual(t, "/", string(prefix[len(prefix)-1]), "prefix %q must not end with /", prefix)
	}
}

func TestServiceRoutes_ReflectsOverriddenURLs(t *testing.T) {
	cfg := loadWith(t, map[string]string{"FILE_SERVICE_URL": "http://file-override:9999"})

	routes := cfg.ServiceRoutes()

	assert.Equal(t, "http://file-override:9999", routes["/api/v1/files"])
	assert.Equal(t, "http://file-override:9999", routes["/api/v1/folders"])
	assert.Equal(t, "http://document-service:8083", routes["/api/v1/documents"], "other services keep their defaults")
}

func TestServiceRoutes_IsANewMapEachCall(t *testing.T) {
	cfg := loadWith(t, nil)

	first := cfg.ServiceRoutes()
	first["/api/v1/files"] = "http://mutated"

	assert.Equal(t, "http://file-service:8082", cfg.ServiceRoutes()["/api/v1/files"],
		"mutating a returned map must not affect later calls")
}
