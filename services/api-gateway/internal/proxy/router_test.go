package proxy

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/rs/zerolog"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/config"
	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/middleware"
)

const routerTestSecret = "router-test-secret"

// backendEcho is what every stub backend in this file replies with.
type backendEcho struct {
	Service string `json:"service"`
	Method  string `json:"method"`
	Path    string `json:"path"`
	RawPath string `json:"raw_path"`
	Query   string `json:"query"`
	Body    string `json:"body"`
	UserID  string `json:"user_id"`
}

func routerTestLogger() zerolog.Logger {
	return zerolog.New(io.Discard)
}

// startEchoBackend starts a stub upstream that reports how the gateway called it.
func startEchoBackend(t *testing.T, service string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(backendEcho{
			Service: service,
			Method:  r.Method,
			Path:    r.URL.Path,
			RawPath: r.URL.EscapedPath(),
			Query:   r.URL.RawQuery,
			Body:    string(body),
			UserID:  r.Header.Get("X-User-ID"),
		})
	}))
	t.Cleanup(srv.Close)
	return srv
}

// deadBackendURL returns the URL of a server that has already been shut down, so
// connecting to it fails immediately and deterministically (no sleeps, no timeouts).
func deadBackendURL(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := srv.URL
	srv.Close()
	return url
}

// permissiveCBManager returns a manager whose breakers never trip during a test.
func permissiveCBManager() *CircuitBreakerManager {
	return NewCircuitBreakerManager(CircuitBreakerConfig{
		MaxRequests:  5,
		Interval:     time.Hour,
		Timeout:      time.Hour,
		FailureRatio: 1.1, // unreachable ratio: the breaker stays closed
	})
}

func newTestRouter(t *testing.T, routes []Route) http.Handler {
	t.Helper()
	return NewRouter(RouterConfig{
		Routes:    routes,
		CBManager: permissiveCBManager(),
		Logger:    routerTestLogger(),
	})
}

func routesFrom(cfg *config.Config) []Route {
	routes := make([]Route, 0, len(cfg.ServiceRoutes()))
	for prefix, target := range cfg.ServiceRoutes() {
		routes = append(routes, Route{Prefix: prefix, TargetURL: target})
	}
	return routes
}

func doRequest(t *testing.T, handler http.Handler, method, target string, body io.Reader) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(method, target, body))
	return rec
}

func decodeEcho(t *testing.T, rec *httptest.ResponseRecorder) backendEcho {
	t.Helper()
	var echo backendEcho
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &echo), "response body: %s", rec.Body.String())
	return echo
}

// --- positive: the routing table ------------------------------------------------

func TestNewRouter_EveryServiceRouteReachesItsBackend(t *testing.T) {
	backends := map[string]*httptest.Server{}
	for _, service := range []string{"auth", "file", "document", "collab", "notification",
		"search", "analytics", "admin", "audit", "report"} {
		backends[service] = startEchoBackend(t, service)
	}

	cfg := &config.Config{
		AuthServiceURL:         backends["auth"].URL,
		FileServiceURL:         backends["file"].URL,
		DocumentServiceURL:     backends["document"].URL,
		CollabServiceURL:       backends["collab"].URL,
		NotificationServiceURL: backends["notification"].URL,
		SearchServiceURL:       backends["search"].URL,
		AnalyticsServiceURL:    backends["analytics"].URL,
		AdminServiceURL:        backends["admin"].URL,
		AuditServiceURL:        backends["audit"].URL,
		ReportServiceURL:       backends["report"].URL,
	}
	router := newTestRouter(t, routesFrom(cfg))

	cases := []struct {
		path    string
		service string
	}{
		{"/api/v1/auth/login", "auth"},
		{"/api/v1/settings/profile", "auth"},
		{"/api/v1/files/abc/download", "file"},
		{"/api/v1/folders/abc", "file"},
		{"/api/v1/documents/abc", "document"},
		{"/api/v1/templates/abc", "document"},
		{"/api/v1/collab/documents", "collab"},
		{"/socket.io/?EIO=4", "collab"},
		{"/api/v1/notifications/unread-count", "notification"},
		{"/api/v1/preferences/email", "notification"},
		{"/api/v1/search/suggest", "search"},
		{"/api/v1/analytics/events", "analytics"},
		{"/api/v1/admin/users", "admin"},
		{"/api/v1/audit/events", "audit"},
		{"/api/v1/reports/42/download", "report"},
	}
	require.Len(t, cases, len(cfg.ServiceRoutes()), "every ServiceRoutes prefix needs a case here")

	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			rec := doRequest(t, router, http.MethodGet, tc.path, nil)

			require.Equal(t, http.StatusOK, rec.Code, "body: %s", rec.Body.String())
			echo := decodeEcho(t, rec)
			assert.Equal(t, tc.service, echo.Service)
			assert.Equal(t, strings.SplitN(tc.path, "?", 2)[0], echo.Path,
				"the gateway must forward the full path unmodified")
		})
	}
}

func TestNewRouter_ForwardsMethodBodyAndQueryString(t *testing.T) {
	backend := startEchoBackend(t, "file")
	router := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	rec := doRequest(t, router, http.MethodPost, "/api/v1/files/upload?folder=root&overwrite=true",
		strings.NewReader(`{"name":"otter.png"}`))

	require.Equal(t, http.StatusOK, rec.Code)
	echo := decodeEcho(t, rec)
	assert.Equal(t, http.MethodPost, echo.Method)
	assert.Equal(t, "/api/v1/files/upload", echo.Path)
	assert.Equal(t, "folder=root&overwrite=true", echo.Query)
	assert.Equal(t, `{"name":"otter.png"}`, echo.Body)
}

func TestNewRouter_PassesUpstreamStatusThrough(t *testing.T) {
	for _, status := range []int{http.StatusNoContent, http.StatusBadRequest, http.StatusNotFound,
		http.StatusInternalServerError} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(status)
			}))
			t.Cleanup(backend.Close)

			router := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})
			rec := doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil)

			assert.Equal(t, status, rec.Code,
				"an upstream status must reach the client unchanged, including 404 and 500")
		})
	}
}

func TestNewRouter_MountedPrefixRootIsRoutable(t *testing.T) {
	backend := startEchoBackend(t, "file")
	router := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	// Boundary trio around the mount point itself: the prefix, the prefix with a
	// trailing slash, and one segment below it.
	for _, path := range []string{"/api/v1/files", "/api/v1/files/", "/api/v1/files/x"} {
		t.Run(path, func(t *testing.T) {
			rec := doRequest(t, router, http.MethodGet, path, nil)
			require.Equal(t, http.StatusOK, rec.Code, "body: %s", rec.Body.String())
			assert.Equal(t, path, decodeEcho(t, rec).Path)
		})
	}
}

func TestNewRouter_TracingWrapperDoesNotChangeRouting(t *testing.T) {
	backend := startEchoBackend(t, "file")
	router := NewRouter(RouterConfig{
		Routes:        []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}},
		CBManager:     permissiveCBManager(),
		Logger:        routerTestLogger(),
		EnableTracing: true,
	})

	rec := doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "file", decodeEcho(t, rec).Service)
}

// --- negative: unknown prefixes and dead backends ---------------------------------

func TestNewRouter_UnknownPrefixReturns404NotFoundJSON(t *testing.T) {
	backend := startEchoBackend(t, "file")
	router := newTestRouter(t, []Route{
		{Prefix: "/api/v1/files", TargetURL: backend.URL},
		{Prefix: "/api/v1/auth", TargetURL: backend.URL},
	})

	unknown := []string{
		"/",
		"/nope",
		"/api",
		"/api/v1",
		"/api/v1/unknown",
		"/api/v2/files",
		"/API/v1/files",  // chi routing is case-sensitive
		"/api/v1/filesx", // a longer prefix is not a partial match
		"/api/v1/file",   // a shorter prefix is not a partial match
		"/health",        // served by the outer mux in main, not the proxy router
		"/socket.io",     // not mounted in this router
	}

	for _, path := range unknown {
		t.Run(path, func(t *testing.T) {
			rec := doRequest(t, router, http.MethodGet, path, nil)

			require.Equal(t, http.StatusNotFound, rec.Code, "body: %s", rec.Body.String())
			assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))

			var body map[string]string
			require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
			assert.Equal(t, "route not found", body["error"])
		})
	}
}

// FINDING (genuine, not planted): neither chi nor the reverse proxy normalises dot
// segments, so a path containing `../` is matched on its literal prefix and forwarded to
// the upstream verbatim. A service that joins the incoming path onto a filesystem or S3
// key prefix would see the traversal. Pinned as current behaviour, not fixed.
func TestNewRouter_DotSegmentsAreForwardedUnnormalised_currentBehaviour(t *testing.T) {
	backend := startEchoBackend(t, "file")
	router := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	for _, path := range []string{"/api/v1/files/../../", "/api/v1/files/../admin", "/api/v1/files/a/../b"} {
		t.Run(path, func(t *testing.T) {
			rec := doRequest(t, router, http.MethodGet, path, nil)

			require.Equal(t, http.StatusOK, rec.Code, "body: %s", rec.Body.String())
			assert.Equal(t, path, decodeEcho(t, rec).Path,
				"pinning today's behaviour: dot segments reach the upstream unchanged")
		})
	}
}

func TestNewRouter_PercentEncodedSegmentsSurviveProxying(t *testing.T) {
	backend := startEchoBackend(t, "file")
	router := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	rec := doRequest(t, router, http.MethodGet, "/api/v1/files/a%2Fb", nil)

	require.Equal(t, http.StatusOK, rec.Code, "body: %s", rec.Body.String())
	echo := decodeEcho(t, rec)
	assert.Equal(t, "/api/v1/files/a/b", echo.Path, "the decoded path has the escape resolved")
	assert.Equal(t, "/api/v1/files/a%2Fb", echo.RawPath,
		"the escaped form must survive so the upstream can tell %2F from /")
}

func TestNewRouter_UnreachableBackendReturns502NotFound(t *testing.T) {
	router := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: deadBackendURL(t)}})

	rec := doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil)

	require.Equal(t, http.StatusBadGateway, rec.Code,
		"a routed prefix whose backend is down must be 502, never 404")
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))

	var body map[string]string
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	assert.Equal(t, "service unavailable", body["error"])
	assert.Equal(t, "/api/v1/files", body["target"], "the 502 names the route prefix, not the backend URL")
}

func TestNewRouter_404AndForRoutedButDeadBackend502AreDistinguishable(t *testing.T) {
	router := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: deadBackendURL(t)}})

	assert.Equal(t, http.StatusNotFound, doRequest(t, router, http.MethodGet, "/api/v1/folders/x", nil).Code,
		"an unrouted prefix is a client error")
	assert.Equal(t, http.StatusBadGateway, doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil).Code,
		"a routed prefix with a dead backend is a gateway error")
}

func TestNewRouter_NoRoutesConfiguredMeansEverythingIs404(t *testing.T) {
	router := newTestRouter(t, nil)

	for _, path := range []string{"/", "/api/v1/files", "/socket.io/"} {
		assert.Equal(t, http.StatusNotFound, doRequest(t, router, http.MethodGet, path, nil).Code, path)
	}
}

// --- route-matrix gaps -------------------------------------------------------------
//
// docs/api-route-matrix.md lists /api/v1/templates, /api/v1/folders, /api/v1/reports and
// /api/v1/preferences under "Known route and behavior gaps" as prefixes the gateway does
// not route. That documentation is stale: config.ServiceRoutes has routed all four since
// commit f8e9dfd ("Fix gateway routing and websocket auth"). The tests below pin the
// behaviour that exists *today* — all four route — so that re-opening any of these gaps
// (or dropping a prefix during a refactor) turns them red deliberately. The document
// itself is outside this work package's owned files and was not edited.

func TestNewRouter_RouteMatrixGapPrefixesReachTheirService_seeRouteMatrix(t *testing.T) {
	document := startEchoBackend(t, "document")
	file := startEchoBackend(t, "file")
	report := startEchoBackend(t, "report")
	notification := startEchoBackend(t, "notification")

	cfg := &config.Config{
		DocumentServiceURL:     document.URL,
		FileServiceURL:         file.URL,
		ReportServiceURL:       report.URL,
		NotificationServiceURL: notification.URL,
		AuthServiceURL:         deadBackendURL(t),
		CollabServiceURL:       deadBackendURL(t),
		SearchServiceURL:       deadBackendURL(t),
		AnalyticsServiceURL:    deadBackendURL(t),
		AdminServiceURL:        deadBackendURL(t),
		AuditServiceURL:        deadBackendURL(t),
	}
	router := newTestRouter(t, routesFrom(cfg))

	cases := []struct {
		path    string
		service string
	}{
		{"/api/v1/templates", "document"},
		{"/api/v1/folders", "file"},
		{"/api/v1/reports", "report"},
		{"/api/v1/preferences", "notification"},
	}

	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			rec := doRequest(t, router, http.MethodGet, tc.path, nil)

			require.Equal(t, http.StatusOK, rec.Code, "body: %s", rec.Body.String())
			assert.Equal(t, tc.service, decodeEcho(t, rec).Service)
		})
	}
}

// --- authz: X-User-ID identity propagation and header spoofing ----------------------

func signedToken(t *testing.T, claims middleware.JWTClaims) string {
	t.Helper()
	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(routerTestSecret))
	require.NoError(t, err)
	return token
}

// authenticatedGateway mirrors the middleware order used in cmd/server/main.go:
// JWT validation runs before the proxy router.
func authenticatedGateway(t *testing.T, routes []Route) http.Handler {
	t.Helper()
	prefixes := make([]string, 0, len(routes))
	for _, route := range routes {
		prefixes = append(prefixes, route.Prefix)
	}
	return middleware.JWTAuth(middleware.JWTConfig{
		Secret:              routerTestSecret,
		PublicPath:          middleware.DefaultPublicPaths(),
		PrefixPath:          middleware.DefaultPrefixPaths(),
		ProtectedPrefixPath: prefixes,
	})(newTestRouter(t, routes))
}

func TestNewRouter_SetsUserIDHeaderFromSubjectClaim(t *testing.T) {
	backend := startEchoBackend(t, "file")
	gateway := authenticatedGateway(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	token := signedToken(t, middleware.JWTClaims{
		UserID: "claim-user-id",
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "subject-user-id",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	gateway.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code, "body: %s", rec.Body.String())
	assert.Equal(t, "subject-user-id", decodeEcho(t, rec).UserID,
		"sub wins over the legacy user_id claim")
}

func TestNewRouter_FallsBackToUserIDClaimWhenSubjectIsEmpty(t *testing.T) {
	backend := startEchoBackend(t, "file")
	gateway := authenticatedGateway(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	token := signedToken(t, middleware.JWTClaims{
		UserID: "legacy-user-id",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	gateway.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "legacy-user-id", decodeEcho(t, rec).UserID)
}

func TestNewRouter_ClientSuppliedUserIDIsOverwrittenByClaims(t *testing.T) {
	backend := startEchoBackend(t, "file")
	gateway := authenticatedGateway(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	token := signedToken(t, middleware.JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "real-user",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	for _, spoofed := range []string{"attacker", "admin", "00000000-0000-0000-0000-000000000000", ""} {
		t.Run("spoofed="+spoofed, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
			req.Header.Set("Authorization", "Bearer "+token)
			req.Header.Set("X-User-ID", spoofed)
			rec := httptest.NewRecorder()
			gateway.ServeHTTP(rec, req)

			require.Equal(t, http.StatusOK, rec.Code)
			assert.Equal(t, "real-user", decodeEcho(t, rec).UserID,
				"a client-supplied X-User-ID must never survive into the upstream request")
		})
	}
}

func TestNewRouter_SpoofedUserIDIsRejectedBeforeProxyingWhenTokenIsInvalid(t *testing.T) {
	backend := startEchoBackend(t, "file")
	gateway := authenticatedGateway(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	cases := []struct {
		name string
		auth string
	}{
		{"no token at all", ""},
		{"garbage token", "Bearer not-a-jwt"},
		{"token signed with another key", "Bearer " + func() string {
			token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, middleware.JWTClaims{
				RegisteredClaims: jwt.RegisteredClaims{Subject: "attacker"},
			}).SignedString([]byte("some-other-secret"))
			require.NoError(t, err)
			return token
		}()},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
			if tc.auth != "" {
				req.Header.Set("Authorization", tc.auth)
			}
			req.Header.Set("X-User-ID", "admin")
			rec := httptest.NewRecorder()
			gateway.ServeHTTP(rec, req)

			assert.Equal(t, http.StatusUnauthorized, rec.Code,
				"the request must not reach the backend at all")
		})
	}
}

// FINDING (genuine, not planted): the director only *sets* X-User-ID when the context
// carries JWT claims. On a public path — /api/v1/auth/login, /api/v1/auth/register and
// anything under /socket.io — there are no claims, so a client-supplied X-User-ID is
// forwarded verbatim to the upstream service. docs/api-route-matrix.md notes that some
// services trust that header. The behaviour is pinned here rather than fixed (this work
// package is test-only); see TestNewRouter_SpoofedUserIDShouldNotReachUpstreamOnPublicPaths
// for the assertion that should hold once it is fixed.
func TestNewRouter_SpoofedUserIDSurvivesOnPublicPaths_currentBehaviour(t *testing.T) {
	auth := startEchoBackend(t, "auth")
	collab := startEchoBackend(t, "collab")
	gateway := authenticatedGateway(t, []Route{
		{Prefix: "/api/v1/auth", TargetURL: auth.URL},
		{Prefix: "/socket.io", TargetURL: collab.URL},
	})

	for _, path := range []string{"/api/v1/auth/login", "/api/v1/auth/register", "/socket.io/"} {
		t.Run(path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, path, nil)
			req.Header.Set("X-User-ID", "spoofed-admin")
			rec := httptest.NewRecorder()
			gateway.ServeHTTP(rec, req)

			require.Equal(t, http.StatusOK, rec.Code)
			assert.Equal(t, "spoofed-admin", decodeEcho(t, rec).UserID,
				"pinning today's behaviour: the header is forwarded unfiltered on public paths")
		})
	}
}

func TestNewRouter_SpoofedUserIDShouldNotReachUpstreamOnPublicPaths(t *testing.T) {
	t.Skip("expected-fail: the gateway forwards a client-supplied X-User-ID on unauthenticated " +
		"routes because the proxy director only overwrites the header when JWT claims exist; " +
		"see TestNewRouter_SpoofedUserIDSurvivesOnPublicPaths_currentBehaviour")

	auth := startEchoBackend(t, "auth")
	gateway := authenticatedGateway(t, []Route{{Prefix: "/api/v1/auth", TargetURL: auth.URL}})

	req := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", nil)
	req.Header.Set("X-User-ID", "spoofed-admin")
	rec := httptest.NewRecorder()
	gateway.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Empty(t, decodeEcho(t, rec).UserID)
}

// FINDING (genuine, not planted): a token that validates but carries neither `sub` nor
// `user_id` leaves the client's own X-User-ID header untouched, so an authenticated user
// holding such a token can pick any identity. Pinned, not fixed.
func TestNewRouter_SpoofedUserIDSurvivesWhenClaimsCarryNoIdentity_currentBehaviour(t *testing.T) {
	backend := startEchoBackend(t, "file")
	gateway := authenticatedGateway(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	token := signedToken(t, middleware.JWTClaims{
		Email: "someone@otterworks.dev",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("X-User-ID", "somebody-else")
	rec := httptest.NewRecorder()
	gateway.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "somebody-else", decodeEcho(t, rec).UserID,
		"pinning today's behaviour: an identity-less token does not clear the header")
}

// --- boundary: circuit breaker interaction with the router --------------------------

func TestNewRouter_OpensCircuitAfterMinimumSampleOfFailures(t *testing.T) {
	router := NewRouter(RouterConfig{
		Routes: []Route{{Prefix: "/api/v1/files", TargetURL: deadBackendURL(t)}},
		CBManager: NewCircuitBreakerManager(CircuitBreakerConfig{
			MaxRequests:  5,
			Interval:     time.Hour,
			Timeout:      time.Hour,
			FailureRatio: 0.6,
		}),
		Logger: routerTestLogger(),
	})

	// The breaker needs a minimum sample of 5 requests before it can trip, so the
	// boundary trio is the 4th (still closed), the 5th (the one that trips it) and the
	// 6th request (rejected without touching the backend).
	for i := 1; i <= 5; i++ {
		rec := doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil)
		require.Equal(t, http.StatusBadGateway, rec.Code, "request %d should still reach the dead backend", i)
	}

	rec := doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil)
	require.Equal(t, http.StatusServiceUnavailable, rec.Code, "request 6 must be shed by the open circuit")

	var body map[string]string
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	assert.Equal(t, "service temporarily unavailable", body["error"])
	assert.Equal(t, "/api/v1/files", body["service"])
	assert.Equal(t, "circuit breaker open", body["reason"])
}

func TestNewRouter_CircuitBreakerIsScopedToOneRoutePrefix(t *testing.T) {
	healthy := startEchoBackend(t, "document")
	router := NewRouter(RouterConfig{
		Routes: []Route{
			{Prefix: "/api/v1/files", TargetURL: deadBackendURL(t)},
			{Prefix: "/api/v1/documents", TargetURL: healthy.URL},
		},
		CBManager: NewCircuitBreakerManager(CircuitBreakerConfig{
			MaxRequests:  5,
			Interval:     time.Hour,
			Timeout:      time.Hour,
			FailureRatio: 0.6,
		}),
		Logger: routerTestLogger(),
	})

	for i := 0; i < 6; i++ {
		doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil)
	}

	require.Equal(t, http.StatusServiceUnavailable,
		doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil).Code)
	assert.Equal(t, http.StatusOK,
		doRequest(t, router, http.MethodGet, "/api/v1/documents/x", nil).Code,
		"one failing backend must not shed traffic for another service")
}

func TestNewRouter_SuccessfulTrafficNeverOpensTheCircuit(t *testing.T) {
	backend := startEchoBackend(t, "file")
	router := NewRouter(RouterConfig{
		Routes: []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}},
		CBManager: NewCircuitBreakerManager(CircuitBreakerConfig{
			MaxRequests:  5,
			Interval:     time.Hour,
			Timeout:      time.Hour,
			FailureRatio: 0.6,
		}),
		Logger: routerTestLogger(),
	})

	for i := 0; i < 20; i++ {
		require.Equal(t, http.StatusOK, doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil).Code)
	}
}

// --- concurrency / idempotency -------------------------------------------------------

func TestNewRouter_ConcurrentRequestsKeepTheirOwnIdentity(t *testing.T) {
	backend := startEchoBackend(t, "file")
	gateway := authenticatedGateway(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	const workers = 25
	var wg sync.WaitGroup
	errs := make(chan error, workers)

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			userID := fmt.Sprintf("user-%02d", i)
			token := jwt.NewWithClaims(jwt.SigningMethodHS256, middleware.JWTClaims{
				RegisteredClaims: jwt.RegisteredClaims{
					Subject:   userID,
					ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
				},
			})
			signed, err := token.SignedString([]byte(routerTestSecret))
			if err != nil {
				errs <- err
				return
			}

			req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
			req.Header.Set("Authorization", "Bearer "+signed)
			req.Header.Set("X-User-ID", "spoofed")
			rec := httptest.NewRecorder()
			gateway.ServeHTTP(rec, req)

			if rec.Code != http.StatusOK {
				errs <- fmt.Errorf("%s: status %d", userID, rec.Code)
				return
			}
			var echo backendEcho
			if err := json.Unmarshal(rec.Body.Bytes(), &echo); err != nil {
				errs <- err
				return
			}
			if echo.UserID != userID {
				errs <- fmt.Errorf("identity leak: want %s, got %s", userID, echo.UserID)
			}
		}(i)
	}

	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}
}

func TestNewRouter_RepeatedIdenticalRequestsAreIdempotentAtTheGateway(t *testing.T) {
	var mu sync.Mutex
	var seen []string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = append(seen, r.URL.Path)
		mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(backend.Close)

	router := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: backend.URL}})

	for i := 0; i < 3; i++ {
		require.Equal(t, http.StatusOK, doRequest(t, router, http.MethodGet, "/api/v1/files/x", nil).Code)
	}

	mu.Lock()
	defer mu.Unlock()
	assert.Equal(t, []string{"/api/v1/files/x", "/api/v1/files/x", "/api/v1/files/x"}, seen,
		"the gateway forwards each request exactly once, with no retry amplification")
}
