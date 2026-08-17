package middleware

import (
	"net/http"
	"strconv"
	"strings"
)

// CORSConfig holds configuration for CORS middleware.
type CORSConfig struct {
	AllowedOrigins   []string
	AllowedMethods   []string
	AllowedHeaders   []string
	ExposedHeaders   []string
	AllowCredentials bool
	MaxAge           int
}

// DefaultCORSConfig returns a default CORS configuration.
// https://localhost and capacitor://localhost are the WebView origins of the
// Capacitor mobile app (Android and iOS respectively).
func DefaultCORSConfig() CORSConfig {
	return CORSConfig{
		AllowedOrigins:   []string{"http://localhost:3000", "http://localhost:4200", "https://localhost", "capacitor://localhost"},
		AllowedMethods:   []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Accept", "Authorization", "Content-Type", "X-Request-ID"},
		ExposedHeaders:   []string{"Link", "X-Request-ID"},
		AllowCredentials: true,
		MaxAge:           300,
	}
}

// corsPolicy is the precomputed form of a CORSConfig used to serve requests.
type corsPolicy struct {
	allowedOrigins   map[string]bool
	allowAnyOrigin   bool
	allowCredentials bool
	methods          string
	headers          string
	exposed          string
	maxAge           string
}

func newCORSPolicy(cfg CORSConfig) corsPolicy {
	p := corsPolicy{
		allowedOrigins:   make(map[string]bool, len(cfg.AllowedOrigins)),
		allowCredentials: cfg.AllowCredentials,
		methods:          strings.Join(cfg.AllowedMethods, ", "),
		headers:          strings.Join(cfg.AllowedHeaders, ", "),
		exposed:          strings.Join(cfg.ExposedHeaders, ", "),
		maxAge:           strconv.Itoa(cfg.MaxAge),
	}
	for _, o := range cfg.AllowedOrigins {
		if o == "*" {
			p.allowAnyOrigin = true
		}
		p.allowedOrigins[o] = true
	}
	return p
}

func (p corsPolicy) allows(origin string) bool {
	return origin != "" && (p.allowAnyOrigin || p.allowedOrigins[origin])
}

func (p corsPolicy) writeOriginHeaders(w http.ResponseWriter, origin string) {
	h := w.Header()
	h.Set("Access-Control-Allow-Origin", origin)
	if p.allowCredentials {
		h.Set("Access-Control-Allow-Credentials", "true")
	}
	if p.exposed != "" {
		h.Set("Access-Control-Expose-Headers", p.exposed)
	}
	h.Set("Vary", "Origin")
}

func (p corsPolicy) writePreflight(w http.ResponseWriter) {
	h := w.Header()
	h.Set("Access-Control-Allow-Methods", p.methods)
	h.Set("Access-Control-Allow-Headers", p.headers)
	h.Set("Access-Control-Max-Age", p.maxAge)
	w.WriteHeader(http.StatusNoContent)
}

// CORS returns an HTTP middleware that handles Cross-Origin Resource Sharing.
func CORS(cfg CORSConfig) func(http.Handler) http.Handler {
	policy := newCORSPolicy(cfg)

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			allowed := policy.allows(origin)

			if allowed {
				policy.writeOriginHeaders(w, origin)
			}

			// Handle preflight (only for allowed origins)
			if allowed && r.Method == http.MethodOptions {
				policy.writePreflight(w)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}
