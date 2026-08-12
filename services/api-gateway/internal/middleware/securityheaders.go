package middleware

import (
	"bufio"
	"errors"
	"net"
	"net/http"
)

// SecurityHeadersConfig holds configuration for the security-headers middleware.
type SecurityHeadersConfig struct {
	ContentSecurityPolicy string
	FrameOptions          string
	ReferrerPolicy        string
	HSTS                  string
}

// DefaultSecurityHeadersConfig returns the baseline policy for API responses.
// The gateway serves JSON, so the CSP only has to forbid every content source.
func DefaultSecurityHeadersConfig() SecurityHeadersConfig {
	return SecurityHeadersConfig{
		ContentSecurityPolicy: "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
		FrameOptions:          "DENY",
		ReferrerPolicy:        "no-referrer",
		HSTS:                  "max-age=31536000; includeSubDomains",
	}
}

// SecurityHeaders returns middleware that sets baseline browser security headers on
// every response, including error responses written by middleware further down the
// stack. The headers are filled in when the status is written, so a value a backend
// supplied wins instead of being duplicated alongside the gateway's.
func SecurityHeaders(cfg SecurityHeadersConfig) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			sw := &securityHeaderWriter{ResponseWriter: w, cfg: cfg, tls: isRequestTLS(r)}
			next.ServeHTTP(sw, r)
			// A handler that never wrote anything still gets the headers.
			sw.apply()
		})
	}
}

type securityHeaderWriter struct {
	http.ResponseWriter
	cfg     SecurityHeadersConfig
	tls     bool
	applied bool
}

func (w *securityHeaderWriter) apply() {
	if w.applied {
		return
	}
	w.applied = true

	h := w.Header()
	setIfAbsent(h, "X-Content-Type-Options", "nosniff")
	setIfAbsent(h, "X-Frame-Options", w.cfg.FrameOptions)
	setIfAbsent(h, "Content-Security-Policy", w.cfg.ContentSecurityPolicy)
	setIfAbsent(h, "Referrer-Policy", w.cfg.ReferrerPolicy)
	// HSTS is only meaningful over TLS. The gateway terminates plaintext behind an
	// ingress, so the forwarded scheme decides.
	if w.cfg.HSTS != "" && w.tls {
		setIfAbsent(h, "Strict-Transport-Security", w.cfg.HSTS)
	}
}

func (w *securityHeaderWriter) WriteHeader(status int) {
	w.apply()
	w.ResponseWriter.WriteHeader(status)
}

func (w *securityHeaderWriter) Write(b []byte) (int, error) {
	w.apply()
	return w.ResponseWriter.Write(b)
}

// Flush keeps streaming responses (SSE, websocket upgrades via the proxy) working.
func (w *securityHeaderWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Hijack keeps the websocket upgrades the collaboration route depends on working.
func (w *securityHeaderWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if h, ok := w.ResponseWriter.(http.Hijacker); ok {
		return h.Hijack()
	}
	return nil, nil, errors.New("securityHeaderWriter: underlying writer does not support hijacking")
}

func (w *securityHeaderWriter) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}

func setIfAbsent(h http.Header, key, value string) {
	if value != "" && h.Get(key) == "" {
		h.Set(key, value)
	}
}

// isRequestTLS reports whether the hop in front of the gateway was TLS.
// X-Forwarded-Proto is only present here when a trusted proxy set it: RealIP strips
// it from untrusted peers, so a plaintext client cannot force an HSTS response.
func isRequestTLS(r *http.Request) bool {
	if r.TLS != nil {
		return true
	}
	return r.Header.Get("X-Forwarded-Proto") == "https"
}
