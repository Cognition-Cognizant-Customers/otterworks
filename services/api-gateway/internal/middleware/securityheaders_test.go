package middleware

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	chimw "github.com/go-chi/chi/v5/middleware"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
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

// The proxy hijacks the connection for the /socket.io upgrade, and chi's
// WrapResponseWriter only forwards Hijack when the writer it wraps also implements
// http.Flusher and io.ReaderFrom.
func TestSecurityHeaders_KeepsResponseWriterHijackable(t *testing.T) {
	var hijackable, flushable, readerFrom bool

	stack := SecurityHeaders(DefaultSecurityHeadersConfig())(
		http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			wrapped := chimw.NewWrapResponseWriter(w, r.ProtoMajor)
			_, hijackable = wrapped.(http.Hijacker)
			_, flushable = wrapped.(http.Flusher)
			_, readerFrom = wrapped.(io.ReaderFrom)
			w.WriteHeader(http.StatusOK)
		}),
	)

	srv := httptest.NewServer(stack)
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/socket.io/")
	require.NoError(t, err)
	defer resp.Body.Close()

	assert.True(t, hijackable, "chi must still hand the proxy a hijackable writer")
	assert.True(t, flushable)
	assert.True(t, readerFrom)
}
