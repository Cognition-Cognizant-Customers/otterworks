package proxy

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/rs/zerolog"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"

	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/middleware"
)

const routerTestSecret = "router-test-secret"

func newTestCBManager() *CircuitBreakerManager {
	return NewCircuitBreakerManager(CircuitBreakerConfig{
		MaxRequests:  1,
		Interval:     time.Minute,
		Timeout:      time.Minute,
		FailureRatio: 0.99,
	})
}

func newTestRouter(t *testing.T, backendURL string, enableTracing bool) http.Handler {
	t.Helper()
	return NewRouter(RouterConfig{
		Routes:        []Route{{Prefix: "/api/v1/files", TargetURL: backendURL}},
		CBManager:     newTestCBManager(),
		Logger:        zerolog.Nop(),
		EnableTracing: enableTracing,
	})
}

func signToken(t *testing.T, claims middleware.JWTClaims) string {
	t.Helper()
	tok, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(routerTestSecret))
	require.NoError(t, err)
	return tok
}

func TestRouter_ProxiesToBackendPreservingPath(t *testing.T) {
	var gotPath string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	defer backend.Close()

	router := newTestRouter(t, backend.URL, false)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil))

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "/api/v1/files/list", gotPath)
}

func TestRouter_NotFoundReturnsJSON404(t *testing.T) {
	router := newTestRouter(t, "http://127.0.0.1:1", false)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/unknown", nil))

	assert.Equal(t, http.StatusNotFound, rec.Code)
	var body map[string]string
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	assert.Equal(t, "route not found", body["error"])
}

func TestRouter_BackendDownReturns502(t *testing.T) {
	router := newTestRouter(t, "http://127.0.0.1:1", false)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil))

	assert.Equal(t, http.StatusBadGateway, rec.Code)
	var body map[string]string
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	assert.Equal(t, "service unavailable", body["error"])
}

func TestRouter_ForwardsUserIDFromJWTSubject(t *testing.T) {
	var gotUserID string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUserID = r.Header.Get("X-User-ID")
		w.WriteHeader(http.StatusOK)
	}))
	defer backend.Close()

	router := newTestRouter(t, backend.URL, false)
	handler := middleware.JWTAuth(middleware.JWTConfig{Secret: routerTestSecret})(router)

	token := signToken(t, middleware.JWTClaims{
		UserID: "custom-id",
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "sub-id",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "sub-id", gotUserID, "standard sub claim takes precedence")
}

func TestRouter_ForwardsUserIDFallbackToCustomClaim(t *testing.T) {
	var gotUserID string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUserID = r.Header.Get("X-User-ID")
		w.WriteHeader(http.StatusOK)
	}))
	defer backend.Close()

	router := newTestRouter(t, backend.URL, false)
	handler := middleware.JWTAuth(middleware.JWTConfig{Secret: routerTestSecret})(router)

	token := signToken(t, middleware.JWTClaims{
		UserID: "custom-id",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "custom-id", gotUserID)
}

func TestRouter_TracingEnabledRecordsSpan(t *testing.T) {
	prevTP := otel.GetTracerProvider()
	prevProp := otel.GetTextMapPropagator()
	recorder := tracetest.NewSpanRecorder()
	tp := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	t.Cleanup(func() {
		otel.SetTracerProvider(prevTP)
		otel.SetTextMapPropagator(prevProp)
		_ = tp.Shutdown(t.Context())
	})

	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer backend.Close()

	router := newTestRouter(t, backend.URL, true)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil))

	assert.Equal(t, http.StatusOK, rec.Code)
	spans := recorder.Ended()
	require.NotEmpty(t, spans, "otelhttp handler should record a server span")
	assert.Equal(t, trace.SpanKindServer, spans[0].SpanKind())
}

func TestRouter_CircuitBreakerOpensAfterFailures(t *testing.T) {
	router := NewRouter(RouterConfig{
		Routes: []Route{{Prefix: "/api/v1/files", TargetURL: "http://127.0.0.1:1"}},
		CBManager: NewCircuitBreakerManager(CircuitBreakerConfig{
			MaxRequests:  1,
			Interval:     time.Minute,
			Timeout:      time.Minute,
			FailureRatio: 0.5,
		}),
		Logger: zerolog.Nop(),
	})

	var lastCode int
	for i := 0; i < 20; i++ {
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil))
		lastCode = rec.Code
		if lastCode == http.StatusServiceUnavailable {
			break
		}
	}
	assert.Equal(t, http.StatusServiceUnavailable, lastCode, "circuit breaker should eventually reject requests")
}
