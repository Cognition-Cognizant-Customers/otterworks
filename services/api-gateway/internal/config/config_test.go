package config

import (
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestConfig_ValidateWithEmptyJWTSecret_ReturnsError(t *testing.T) {
	cfg := &Config{JWTSecret: ""}

	err := cfg.Validate()

	require.Error(t, err, "an empty JWT secret must not be accepted: it would make every forged token verifiable")
	assert.Contains(t, err.Error(), "JWT_SECRET")
}

func TestConfig_ValidateWithJWTSecretPresent_ReturnsNil(t *testing.T) {
	cfg := &Config{JWTSecret: "some-secret"}

	assert.NoError(t, cfg.Validate())
}

func TestConfig_LoadWithNoEnvironment_UsesDocumentedDefaults(t *testing.T) {
	clearGatewayEnv(t)

	cfg := Load()

	assert.Equal(t, "8080", cfg.Port)
	assert.Equal(t, "info", cfg.LogLevel)
	assert.Equal(t, 100, cfg.RateLimitRPS)
	assert.Equal(t, 300, cfg.CORSMaxAge)
	assert.Equal(t, 30*time.Second, cfg.ShutdownTimeout)
	assert.Equal(t, uint32(5), cfg.CBMaxRequests)
	assert.Equal(t, 60*time.Second, cfg.CBInterval)
	assert.Equal(t, 30*time.Second, cfg.CBTimeout)
	assert.InDelta(t, 0.6, cfg.CBFailureRatio, 1e-9)
	assert.Equal(t, "", cfg.JWTSecret, "there is no default secret; Validate is the only gate")
}

func TestConfig_LoadWithNoEnvironment_ReturnsUnvalidatableConfig(t *testing.T) {
	clearGatewayEnv(t)

	err := Load().Validate()

	assert.Error(t, err, "a gateway booted with no environment must fail validation, not run with an empty secret")
}

func TestConfig_LoadWithNonNumericInt_FallsBackToDefault(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("RATE_LIMIT_RPS", "not-a-number")
	t.Setenv("CORS_MAX_AGE", "12.5")
	t.Setenv("SHUTDOWN_TIMEOUT_SECONDS", "")

	cfg := Load()

	assert.Equal(t, 100, cfg.RateLimitRPS, "a malformed rate limit is silently ignored rather than failing startup")
	assert.Equal(t, 300, cfg.CORSMaxAge, "a float is not an int and must not be truncated")
	assert.Equal(t, 30*time.Second, cfg.ShutdownTimeout)
}

func TestConfig_LoadWithNonNumericFloat_FallsBackToDefault(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("CB_FAILURE_RATIO", "sixty-percent")

	assert.InDelta(t, 0.6, Load().CBFailureRatio, 1e-9)
}

func TestConfig_LoadWithZeroRateLimit_DisablesAllTraffic(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("RATE_LIMIT_RPS", "0")

	// Zero is a valid integer, so it is accepted and every bucket starts empty.
	// Pinned here because it is indistinguishable from a typo at deploy time.
	assert.Equal(t, 0, Load().RateLimitRPS)
}

func TestConfig_LoadWithNegativeRateLimit_IsAcceptedVerbatim(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("RATE_LIMIT_RPS", "-1")

	assert.Equal(t, -1, Load().RateLimitRPS, "there is no lower-bound check on RATE_LIMIT_RPS")
}

func TestConfig_LoadWithOutOfRangeFailureRatio_IsAcceptedVerbatim(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("CB_FAILURE_RATIO", "2.5")

	// A ratio above 1.0 can never be reached, so the breaker can never trip.
	assert.InDelta(t, 2.5, Load().CBFailureRatio, 1e-9)
}

func TestConfig_LoadWithEmptyCSVSlice_FallsBackToDefault(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("CORS_ALLOWED_ORIGINS", "")

	cfg := Load()

	assert.Contains(t, cfg.CORSAllowedOrigins, "http://localhost:3000")
	assert.NotEmpty(t, cfg.CORSAllowedMethods)
	assert.NotEmpty(t, cfg.CORSAllowedHeaders)
}

func TestConfig_LoadWithSingleValueCSVSlice_ReturnsOneEntry(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")

	assert.Equal(t, []string{"https://app.example.com"}, Load().CORSAllowedOrigins)
}

func TestConfig_LoadWithMultiValueCSVSlice_SplitsOnComma(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("CORS_ALLOWED_ORIGINS", "https://a.example.com,https://b.example.com")

	assert.Equal(t,
		[]string{"https://a.example.com", "https://b.example.com"},
		Load().CORSAllowedOrigins)
}

func TestConfig_LoadWithSpacePaddedCSVSlice_KeepsSurroundingSpace(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("CORS_ALLOWED_ORIGINS", "https://a.example.com, https://b.example.com")

	// getEnvSlice does not trim, so a space-separated list yields an origin that
	// can never match. Pinned so the behaviour is visible rather than surprising.
	assert.Equal(t,
		[]string{"https://a.example.com", " https://b.example.com"},
		Load().CORSAllowedOrigins)
}

func TestConfig_LoadWithOverriddenServiceURL_UsesOverride(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("AUTH_SERVICE_URL", "http://auth.internal:9999")

	cfg := Load()

	assert.Equal(t, "http://auth.internal:9999", cfg.AuthServiceURL)
	assert.Equal(t, "http://file-service:8082", cfg.FileServiceURL, "unset services keep their defaults")
}

func TestConfig_ServiceRoutes_MapsEveryPublicPrefixToABackend(t *testing.T) {
	clearGatewayEnv(t)
	cfg := Load()

	routes := cfg.ServiceRoutes()

	expected := map[string]string{
		"/api/v1/auth":          cfg.AuthServiceURL,
		"/api/v1/files":         cfg.FileServiceURL,
		"/api/v1/folders":       cfg.FileServiceURL,
		"/api/v1/documents":     cfg.DocumentServiceURL,
		"/api/v1/templates":     cfg.DocumentServiceURL,
		"/api/v1/collab":        cfg.CollabServiceURL,
		"/socket.io":            cfg.CollabServiceURL,
		"/api/v1/notifications": cfg.NotificationServiceURL,
		"/api/v1/preferences":   cfg.NotificationServiceURL,
		"/api/v1/search":        cfg.SearchServiceURL,
		"/api/v1/analytics":     cfg.AnalyticsServiceURL,
		"/api/v1/admin":         cfg.AdminServiceURL,
		"/api/v1/audit":         cfg.AuditServiceURL,
		"/api/v1/reports":       cfg.ReportServiceURL,
		"/api/v1/settings":      cfg.AuthServiceURL,
	}
	assert.Equal(t, expected, routes)

	for prefix, target := range routes {
		assert.NotEmpty(t, target, "route %s must resolve to a backend", prefix)
	}
}

func TestConfig_ServiceRoutes_ReflectsOverriddenBackend(t *testing.T) {
	clearGatewayEnv(t)
	t.Setenv("FILE_SERVICE_URL", "http://files.internal:1234")

	routes := Load().ServiceRoutes()

	assert.Equal(t, "http://files.internal:1234", routes["/api/v1/files"])
	assert.Equal(t, "http://files.internal:1234", routes["/api/v1/folders"],
		"/folders and /files share one backend")
}

// clearGatewayEnv unsets every variable Load reads so a test observes defaults
// regardless of what the surrounding environment happens to define. Each key is
// routed through t.Setenv first so the framework registers a cleanup that restores
// the original value, keeping tests order-independent.
func clearGatewayEnv(t *testing.T) {
	t.Helper()
	for _, key := range []string{
		"PORT", "LOG_LEVEL",
		"AUTH_SERVICE_URL", "FILE_SERVICE_URL", "DOCUMENT_SERVICE_URL",
		"COLLAB_SERVICE_URL", "NOTIFICATION_SERVICE_URL", "SEARCH_SERVICE_URL",
		"ANALYTICS_SERVICE_URL", "ADMIN_SERVICE_URL", "AUDIT_SERVICE_URL",
		"REPORT_SERVICE_URL",
		"RATE_LIMIT_RPS", "JWT_SECRET",
		"CORS_ALLOWED_ORIGINS", "CORS_ALLOWED_METHODS", "CORS_ALLOWED_HEADERS",
		"CORS_MAX_AGE", "SHUTDOWN_TIMEOUT_SECONDS",
		"CB_MAX_REQUESTS", "CB_INTERVAL_SECONDS", "CB_TIMEOUT_SECONDS",
		"CB_FAILURE_RATIO",
	} {
		t.Setenv(key, "")
		require.NoError(t, os.Unsetenv(key))
	}
}
