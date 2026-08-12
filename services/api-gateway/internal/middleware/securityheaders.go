package middleware

import "net/http"

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
// stack. Headers already set by a backend are preserved.
func SecurityHeaders(cfg SecurityHeadersConfig) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			h := w.Header()
			setIfAbsent(h, "X-Content-Type-Options", "nosniff")
			setIfAbsent(h, "X-Frame-Options", cfg.FrameOptions)
			setIfAbsent(h, "Content-Security-Policy", cfg.ContentSecurityPolicy)
			setIfAbsent(h, "Referrer-Policy", cfg.ReferrerPolicy)
			// HSTS is only meaningful over TLS. The gateway terminates plaintext behind
			// an ingress, so the forwarded scheme decides.
			if cfg.HSTS != "" && isRequestTLS(r) {
				setIfAbsent(h, "Strict-Transport-Security", cfg.HSTS)
			}
			next.ServeHTTP(w, r)
		})
	}
}

func setIfAbsent(h http.Header, key, value string) {
	if value != "" && h.Get(key) == "" {
		h.Set(key, value)
	}
}

func isRequestTLS(r *http.Request) bool {
	if r.TLS != nil {
		return true
	}
	return r.Header.Get("X-Forwarded-Proto") == "https"
}
