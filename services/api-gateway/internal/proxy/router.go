package proxy

import (
	"encoding/json"
	"net/http"
	"net/http/httputil"
	"net/url"

	"github.com/go-chi/chi/v5"
	"github.com/rs/zerolog"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/middleware"
)

// identityHeaders are set from validated claims and never accepted from a client.
var identityHeaders = []string{"X-User-ID", "X-User-Email", "X-User-Roles"}

// Route defines a mapping from a URL prefix to a backend service.
type Route struct {
	Prefix    string
	TargetURL string
}

// RouterConfig holds configuration for the reverse proxy router.
type RouterConfig struct {
	Routes         []Route
	CBManager      *CircuitBreakerManager
	Logger         zerolog.Logger
	EnableTracing  bool
}

// NewRouter creates a chi router with all service routes mounted.
func NewRouter(cfg RouterConfig) chi.Router {
	r := chi.NewRouter()

	for _, route := range cfg.Routes {
		route := route // capture loop var
		r.Route(route.Prefix, func(sub chi.Router) {
			handler := newProxyHandler(route, cfg)
			sub.HandleFunc("/*", handler)
			sub.HandleFunc("/", handler)
		})
	}
	r.NotFound(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "route not found",
		})
	})

	return r
}

func newProxyHandler(route Route, cfg RouterConfig) http.HandlerFunc {
	// The full request path (e.g. /api/v1/auth/register) is preserved in
	// req.URL.Path, so the target URL should be just the backend host.
	// httputil.ReverseProxy joins the target path with req.URL.Path.
	target, err := url.Parse(route.TargetURL)
	if err != nil {
		cfg.Logger.Fatal().Err(err).Str("target", route.TargetURL).Msg("invalid proxy target URL")
	}

	// Rewrite (rather than Director) so the outbound headers set here are the ones
	// the backend sees, and so the forwarding headers are rebuilt from the peer.
	// The auth-service issues JWTs with the user ID in the standard "sub" claim
	// (claims.Subject). Fall back to the custom "user_id" claim for compatibility.
	proxy := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(target)
			pr.Out.Host = pr.In.Host

			// SetXForwarded derives the scheme from this hop, which is plaintext behind
			// the TLS-terminating ingress. Keep the scheme the ingress reported; the
			// RealIP middleware has already dropped it if an untrusted peer sent it.
			forwardedProto := pr.In.Header.Get("X-Forwarded-Proto")
			pr.SetXForwarded()
			if forwardedProto != "" {
				pr.Out.Header.Set("X-Forwarded-Proto", forwardedProto)
			}

			// Identity headers are the gateway's to set: drop whatever the client sent
			// before deriving them from validated claims.
			for _, h := range identityHeaders {
				pr.Out.Header.Del(h)
			}
			if claims := middleware.GetJWTClaims(pr.In.Context()); claims != nil {
				userID := claims.Subject
				if userID == "" {
					userID = claims.UserID
				}
				if userID != "" {
					pr.Out.Header.Set("X-User-ID", userID)
				}
			}
		},
	}

	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		cfg.Logger.Error().
			Err(err).
			Str("target", route.TargetURL).
			Str("path", r.URL.Path).
			Str("method", r.Method).
			Msg("proxy error")

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		json.NewEncoder(w).Encode(map[string]string{
			"error":  "service unavailable",
			"target": route.Prefix,
		})
	}

	var handler http.Handler = proxy
	if cfg.EnableTracing {
		handler = otelhttp.NewHandler(proxy, "proxy:"+route.Prefix)
	}

	cb := cfg.CBManager.Get(route.Prefix)

	return func(w http.ResponseWriter, r *http.Request) {
		if err := cb.Execute(handler, w, r); err != nil {
			cfg.Logger.Warn().
				Str("circuit_breaker", route.Prefix).
				Str("state", cb.State().String()).
				Msg("circuit breaker rejected request")

			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{
				"error":   "service temporarily unavailable",
				"service": route.Prefix,
				"reason":  "circuit breaker open",
			})
		}
	}
}
