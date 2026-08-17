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

// corsHandler holds the precomputed header values derived from a CORSConfig.
type corsHandler struct {
	allowedOrigins   map[string]bool
	allowAnyOrigin   bool
	allowCredentials bool
	methodsStr       string
	headersStr       string
	exposedStr       string
	maxAgeStr        string
}

func newCORSHandler(cfg CORSConfig) *corsHandler {
	h := &corsHandler{
		allowedOrigins:   make(map[string]bool, len(cfg.AllowedOrigins)),
		allowCredentials: cfg.AllowCredentials,
		methodsStr:       strings.Join(cfg.AllowedMethods, ", "),
		headersStr:       strings.Join(cfg.AllowedHeaders, ", "),
		exposedStr:       strings.Join(cfg.ExposedHeaders, ", "),
		maxAgeStr:        strconv.Itoa(cfg.MaxAge),
	}
	for _, o := range cfg.AllowedOrigins {
		if o == "*" {
			h.allowAnyOrigin = true
		}
		h.allowedOrigins[o] = true
	}
	return h
}

func (h *corsHandler) isOriginAllowed(origin string) bool {
	return origin != "" && (h.allowAnyOrigin || h.allowedOrigins[origin])
}

func (h *corsHandler) writeOriginHeaders(w http.ResponseWriter, origin string) {
	w.Header().Set("Access-Control-Allow-Origin", origin)
	if h.allowCredentials {
		w.Header().Set("Access-Control-Allow-Credentials", "true")
	}
	if h.exposedStr != "" {
		w.Header().Set("Access-Control-Expose-Headers", h.exposedStr)
	}
	w.Header().Set("Vary", "Origin")
}

func (h *corsHandler) writePreflight(w http.ResponseWriter) {
	w.Header().Set("Access-Control-Allow-Methods", h.methodsStr)
	w.Header().Set("Access-Control-Allow-Headers", h.headersStr)
	w.Header().Set("Access-Control-Max-Age", h.maxAgeStr)
	w.WriteHeader(http.StatusNoContent)
}

// CORS returns an HTTP middleware that handles Cross-Origin Resource Sharing.
func CORS(cfg CORSConfig) func(http.Handler) http.Handler {
	h := newCORSHandler(cfg)

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")

			if h.isOriginAllowed(origin) {
				h.writeOriginHeaders(w, origin)

				// Handle preflight (only for allowed origins)
				if r.Method == http.MethodOptions {
					h.writePreflight(w)
					return
				}
			}

			next.ServeHTTP(w, r)
		})
	}
}
