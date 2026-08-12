package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
)

func serveSecurityHeaders(t *testing.T, requestHeaders map[string]string, next http.HandlerFunc) http.Header {
	t.Helper()

	if next == nil {
		next = func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) }
	}
	handler := SecurityHeaders(DefaultSecurityHeadersConfig())(next)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/documents/", nil)
	for k, v := range requestHeaders {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec.Header()
}

func TestSecurityHeaders_BaselineSet(t *testing.T) {
	headers := serveSecurityHeaders(t, nil, nil)

	assert.Equal(t, "nosniff", headers.Get("X-Content-Type-Options"))
	assert.Equal(t, "DENY", headers.Get("X-Frame-Options"))
	assert.NotEmpty(t, headers.Get("Content-Security-Policy"))
	assert.Equal(t, "no-referrer", headers.Get("Referrer-Policy"))
	assert.Empty(t, headers.Get("Strict-Transport-Security"), "HSTS is meaningless on a plaintext hop")
}

func TestSecurityHeaders_HSTSOnForwardedTLS(t *testing.T) {
	headers := serveSecurityHeaders(t, map[string]string{"X-Forwarded-Proto": "https"}, nil)

	assert.Contains(t, headers.Get("Strict-Transport-Security"), "max-age=")
}

func TestSecurityHeaders_PreservesBackendValue(t *testing.T) {
	headers := serveSecurityHeaders(t, nil, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'self'")
		w.WriteHeader(http.StatusOK)
	})

	assert.Equal(t, "default-src 'self'", headers.Get("Content-Security-Policy"))
}

func TestSecurityHeaders_NoDuplicateWhenBackendAddsHeader(t *testing.T) {
	// httputil.ReverseProxy copies upstream headers with Add, not Set.
	headers := serveSecurityHeaders(t, nil, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Add("X-Frame-Options", "SAMEORIGIN")
		w.WriteHeader(http.StatusOK)
	})

	assert.Equal(t, []string{"SAMEORIGIN"}, headers.Values("X-Frame-Options"))
}
