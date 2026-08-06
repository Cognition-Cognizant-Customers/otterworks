package proxy

import (
	"encoding/json"
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

// capturedRequest is a snapshot of what an upstream service actually received.
type capturedRequest struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     string
}

// upstream is a stub backend service that records every request it receives.
// Each test builds its own instances, so no state is shared between tests.
type upstream struct {
	*httptest.Server

	mu       sync.Mutex
	name     string
	status   int
	requests []capturedRequest
}

func newUpstream(t *testing.T, name string) *upstream {
	t.Helper()
	u := &upstream{name: name, status: http.StatusOK}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		u.mu.Lock()
		u.requests = append(u.requests, capturedRequest{
			Method:   r.Method,
			Path:     r.URL.Path,
			RawQuery: r.URL.RawQuery,
			Header:   r.Header.Clone(),
			Body:     string(body),
		})
		status := u.status
		u.mu.Unlock()

		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Upstream-Name", name)
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(map[string]string{"service": name})
	}))
	t.Cleanup(u.Server.Close)
	return u
}

func (u *upstream) setStatus(status int) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.status = status
}

func (u *upstream) count() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return len(u.requests)
}

func (u *upstream) last(t *testing.T) capturedRequest {
	t.Helper()
	u.mu.Lock()
	defer u.mu.Unlock()
	require.NotEmpty(t, u.requests, "upstream %s received no request", u.name)
	return u.requests[len(u.requests)-1]
}

// newTestRouter builds a router over the given routes with a silent logger and
// a circuit breaker that will not trip during a handful of successful calls.
func newTestRouter(t *testing.T, routes []Route) http.Handler {
	t.Helper()
	return NewRouter(RouterConfig{
		Routes: routes,
		CBManager: NewCircuitBreakerManager(CircuitBreakerConfig{
			MaxRequests:  5,
			Interval:     time.Hour,
			Timeout:      time.Hour,
			FailureRatio: 0.6,
		}),
		Logger:        zerolog.Nop(),
		EnableTracing: false,
	})
}

func doRequest(t *testing.T, h http.Handler, method, target string, body io.Reader) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, target, body)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

// --- positive: routing table ------------------------------------------------

func TestRouter_KnownPrefixReachesCorrectUpstream(t *testing.T) {
	files := newUpstream(t, "file-service")
	docs := newUpstream(t, "document-service")

	r := newTestRouter(t, []Route{
		{Prefix: "/api/v1/files", TargetURL: files.URL},
		{Prefix: "/api/v1/documents", TargetURL: docs.URL},
	})

	cases := []struct {
		path     string
		expected *upstream
		other    *upstream
	}{
		{"/api/v1/files/abc", files, docs},
		{"/api/v1/documents/abc", docs, files},
	}

	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			rec := doRequest(t, r, http.MethodGet, tc.path, nil)
			assert.Equal(t, http.StatusOK, rec.Code)
			assert.Equal(t, tc.expected.name, rec.Header().Get("X-Upstream-Name"))
			assert.Equal(t, tc.path, tc.expected.last(t).Path, "full path must be preserved upstream")
		})
	}
}

func TestRouter_PrefixIsolation_OtherUpstreamNeverCalled(t *testing.T) {
	files := newUpstream(t, "file-service")
	docs := newUpstream(t, "document-service")

	r := newTestRouter(t, []Route{
		{Prefix: "/api/v1/files", TargetURL: files.URL},
		{Prefix: "/api/v1/documents", TargetURL: docs.URL},
	})

	doRequest(t, r, http.MethodGet, "/api/v1/files/one", nil)

	assert.Equal(t, 1, files.count())
	assert.Equal(t, 0, docs.count(), "a /files request must not reach the document service")
}

func TestRouter_MethodPassThrough(t *testing.T) {
	files := newUpstream(t, "file-service")
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	methods := []string{
		http.MethodGet, http.MethodPost, http.MethodPut,
		http.MethodPatch, http.MethodDelete, http.MethodHead, http.MethodOptions,
	}

	for _, m := range methods {
		t.Run(m, func(t *testing.T) {
			rec := doRequest(t, r, m, "/api/v1/files/x", nil)
			assert.Equal(t, http.StatusOK, rec.Code)
			assert.Equal(t, m, files.last(t).Method)
		})
	}
}

func TestRouter_BodyPassThrough(t *testing.T) {
	docs := newUpstream(t, "document-service")
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/documents", TargetURL: docs.URL}})

	rec := doRequest(t, r, http.MethodPost, "/api/v1/documents", strings.NewReader(`{"title":"otter"}`))

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.JSONEq(t, `{"title":"otter"}`, docs.last(t).Body)
}

func TestRouter_QueryStringPassThrough(t *testing.T) {
	search := newUpstream(t, "search-service")
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/search", TargetURL: search.URL}})

	cases := []struct {
		name  string
		query string
	}{
		{"simple", "q=otter"},
		{"multiple params", "q=otter&page=2&limit=50"},
		{"repeated key", "tag=a&tag=b"},
		{"encoded value", "q=otter%20pup&filter=a%2Bb"},
		{"empty value", "q="},
		{"no query", ""},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			target := "/api/v1/search"
			if tc.query != "" {
				target += "?" + tc.query
			}
			rec := doRequest(t, r, http.MethodGet, target, nil)
			assert.Equal(t, http.StatusOK, rec.Code)
			assert.Equal(t, tc.query, search.last(t).RawQuery)
		})
	}
}

func TestRouter_HeaderPassThrough(t *testing.T) {
	files := newUpstream(t, "file-service")
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("X-Request-ID", "req-42")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Bearer opaque-token")
	req.Header.Add("X-Multi", "one")
	req.Header.Add("X-Multi", "two")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	got := files.last(t).Header
	assert.Equal(t, "req-42", got.Get("X-Request-ID"))
	assert.Equal(t, "application/json", got.Get("Accept"))
	assert.Equal(t, "Bearer opaque-token", got.Get("Authorization"))
	assert.Equal(t, []string{"one", "two"}, got.Values("X-Multi"))
}

func TestRouter_UpstreamStatusAndBodyArePropagated(t *testing.T) {
	admin := newUpstream(t, "admin-service")
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/admin", TargetURL: admin.URL}})

	for _, status := range []int{http.StatusOK, http.StatusCreated, http.StatusForbidden, http.StatusNotFound} {
		admin.setStatus(status)
		rec := doRequest(t, r, http.MethodGet, "/api/v1/admin/users", nil)
		assert.Equal(t, status, rec.Code)
		assert.JSONEq(t, `{"service":"admin-service"}`, rec.Body.String())
	}
}

// --- edge: path shapes ------------------------------------------------------

func TestRouter_PathShapes(t *testing.T) {
	files := newUpstream(t, "file-service")
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	cases := []struct {
		name         string
		path         string
		expectStatus int
		expectPath   string // path the upstream should see; "" means upstream not reached
	}{
		{"bare prefix", "/api/v1/files", http.StatusOK, "/api/v1/files"},
		{"trailing slash", "/api/v1/files/", http.StatusOK, "/api/v1/files/"},
		{"nested", "/api/v1/files/abc/download", http.StatusOK, "/api/v1/files/abc/download"},
		{"double slash inside", "/api/v1/files//abc", http.StatusOK, "/api/v1/files//abc"},
		{"double trailing slash", "/api/v1/files//", http.StatusOK, "/api/v1/files//"},
		{"dot segment", "/api/v1/files/./abc", http.StatusOK, "/api/v1/files/./abc"},
		{"prefix as substring of a longer segment", "/api/v1/filesystem", http.StatusNotFound, ""},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			before := files.count()
			rec := doRequest(t, r, http.MethodGet, tc.path, nil)
			assert.Equal(t, tc.expectStatus, rec.Code)
			if tc.expectPath == "" {
				assert.Equal(t, before, files.count(), "upstream must not be reached for %s", tc.path)
				return
			}
			require.Equal(t, before+1, files.count())
			assert.Equal(t, tc.expectPath, files.last(t).Path)
		})
	}
}

func TestRouter_RootPathIsNotFound(t *testing.T) {
	files := newUpstream(t, "file-service")
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	rec := doRequest(t, r, http.MethodGet, "/", nil)

	assert.Equal(t, http.StatusNotFound, rec.Code)
	assert.Equal(t, 0, files.count())
}

// --- negative: unknown routes ----------------------------------------------

// Pins the 404-vs-502 question: an unrouted prefix is answered by the router's
// own NotFound handler (404 + {"error":"route not found"}), never by a proxy
// attempt that would surface as 502.
func TestRouter_UnknownPrefixIs404NotBadGateway(t *testing.T) {
	files := newUpstream(t, "file-service")
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	unknown := []string{
		"/api/v1/unknown",
		"/api/v1/unknown/deep/path",
		"/api/v2/files",
		"/apiv1/files",
		"/files",
	}

	for _, path := range unknown {
		t.Run(path, func(t *testing.T) {
			rec := doRequest(t, r, http.MethodGet, path, nil)
			require.Equal(t, http.StatusNotFound, rec.Code)
			assert.NotEqual(t, http.StatusBadGateway, rec.Code)
			assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))
			assert.JSONEq(t, `{"error":"route not found"}`, rec.Body.String())
		})
	}
}

func TestRouter_UnknownPrefixIs404ForEveryMethod(t *testing.T) {
	r := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: "http://127.0.0.1:1"}})

	for _, m := range []string{http.MethodGet, http.MethodPost, http.MethodPut, http.MethodDelete, http.MethodPatch} {
		t.Run(m, func(t *testing.T) {
			rec := doRequest(t, r, m, "/api/v1/nope", nil)
			assert.Equal(t, http.StatusNotFound, rec.Code)
		})
	}
}

func TestRouter_EmptyRouteTableAnswers404(t *testing.T) {
	r := newTestRouter(t, nil)

	rec := doRequest(t, r, http.MethodGet, "/api/v1/files", nil)

	assert.Equal(t, http.StatusNotFound, rec.Code)
	assert.JSONEq(t, `{"error":"route not found"}`, rec.Body.String())
}

// A routed prefix whose backend is unreachable is a 502 from the proxy
// ErrorHandler - this is the counterpart to the 404 above.
func TestRouter_RoutedPrefixWithDeadUpstreamIs502(t *testing.T) {
	dead := newUpstream(t, "dead-service")
	deadURL := dead.URL
	dead.Server.Close() // closed before any request: connection refused

	r := newTestRouter(t, []Route{{Prefix: "/api/v1/files", TargetURL: deadURL}})

	rec := doRequest(t, r, http.MethodGet, "/api/v1/files/x", nil)

	assert.Equal(t, http.StatusBadGateway, rec.Code)
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))
	assert.JSONEq(t, `{"error":"service unavailable","target":"/api/v1/files"}`, rec.Body.String())
}

// --- circuit breaker interaction (router's 503 branch) ----------------------

func TestRouter_OpenCircuitBreakerReturns503(t *testing.T) {
	broken := newUpstream(t, "broken-service")
	broken.setStatus(http.StatusInternalServerError)

	// Minimum sample size inside the breaker is 5 requests; with a 0.6 failure
	// ratio, five consecutive 500s trip it. No sleeps: the timeout is an hour,
	// so the breaker stays open for the rest of the test.
	r := NewRouter(RouterConfig{
		Routes: []Route{{Prefix: "/api/v1/files", TargetURL: broken.URL}},
		CBManager: NewCircuitBreakerManager(CircuitBreakerConfig{
			MaxRequests:  5,
			Interval:     time.Hour,
			Timeout:      time.Hour,
			FailureRatio: 0.6,
		}),
		Logger: zerolog.Nop(),
	})

	for i := 0; i < 5; i++ {
		rec := doRequest(t, r, http.MethodGet, "/api/v1/files/x", nil)
		require.Equal(t, http.StatusInternalServerError, rec.Code, "request %d should still reach the upstream", i)
	}
	require.Equal(t, 5, broken.count())

	rec := doRequest(t, r, http.MethodGet, "/api/v1/files/x", nil)

	assert.Equal(t, http.StatusServiceUnavailable, rec.Code)
	assert.JSONEq(t,
		`{"error":"service temporarily unavailable","service":"/api/v1/files","reason":"circuit breaker open"}`,
		rec.Body.String())
	assert.Equal(t, 5, broken.count(), "the rejected request must not reach the upstream")
}

func TestRouter_CircuitBreakersAreScopedPerRoute(t *testing.T) {
	broken := newUpstream(t, "broken-service")
	broken.setStatus(http.StatusInternalServerError)
	healthy := newUpstream(t, "healthy-service")

	r := NewRouter(RouterConfig{
		Routes: []Route{
			{Prefix: "/api/v1/files", TargetURL: broken.URL},
			{Prefix: "/api/v1/documents", TargetURL: healthy.URL},
		},
		CBManager: NewCircuitBreakerManager(CircuitBreakerConfig{
			MaxRequests:  5,
			Interval:     time.Hour,
			Timeout:      time.Hour,
			FailureRatio: 0.6,
		}),
		Logger: zerolog.Nop(),
	})

	for i := 0; i < 6; i++ {
		doRequest(t, r, http.MethodGet, "/api/v1/files/x", nil)
	}

	rec := doRequest(t, r, http.MethodGet, "/api/v1/documents/x", nil)
	assert.Equal(t, http.StatusOK, rec.Code, "a tripped breaker on /files must not affect /documents")
}

func TestRouter_TracingEnabledStillRoutes(t *testing.T) {
	files := newUpstream(t, "file-service")

	r := NewRouter(RouterConfig{
		Routes: []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}},
		CBManager: NewCircuitBreakerManager(CircuitBreakerConfig{
			MaxRequests: 5, Interval: time.Hour, Timeout: time.Hour, FailureRatio: 0.6,
		}),
		Logger:        zerolog.Nop(),
		EnableTracing: true,
	})

	rec := doRequest(t, r, http.MethodGet, "/api/v1/files/x", nil)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "/api/v1/files/x", files.last(t).Path)
}

// --- the four documented route gaps ----------------------------------------
//
// docs/api-route-matrix.md ("Known route and behavior gaps to verify") states
// that /api/v1/templates, /api/v1/folders, /api/v1/reports and
// /api/v1/preferences are served by backing services but absent from
// Config.ServiceRoutes. That is no longer true on main: all four prefixes are
// present in internal/config/config.go and are therefore routed today.
//
// The four tests below pin TODAY's behavior (routed, reaching the documented
// backing service). They are deliberately named after the route matrix so the
// stale documentation is easy to find. If someone ever removes one of these
// prefixes from ServiceRoutes - reopening the gap the matrix describes - the
// corresponding test turns red on purpose.

func TestRouteMatrixGap_Templates_is_currently_routed_see_route_matrix(t *testing.T) {
	assertRouteMatrixPrefix(t, "/api/v1/templates", "DOCUMENT_SERVICE_URL")
}

func TestRouteMatrixGap_Folders_is_currently_routed_see_route_matrix(t *testing.T) {
	assertRouteMatrixPrefix(t, "/api/v1/folders", "FILE_SERVICE_URL")
}

func TestRouteMatrixGap_Reports_is_currently_routed_see_route_matrix(t *testing.T) {
	assertRouteMatrixPrefix(t, "/api/v1/reports", "REPORT_SERVICE_URL")
}

func TestRouteMatrixGap_Preferences_is_currently_routed_see_route_matrix(t *testing.T) {
	assertRouteMatrixPrefix(t, "/api/v1/preferences", "NOTIFICATION_SERVICE_URL")
}

// assertRouteMatrixPrefix asserts that prefix is present in the real
// ServiceRoutes table, points at the service configured by envVar, and that a
// router built from that table actually forwards the request there.
func assertRouteMatrixPrefix(t *testing.T, prefix, envVar string) {
	t.Helper()

	backing := newUpstream(t, envVar)
	t.Setenv(envVar, backing.URL)

	routes := config.Load().ServiceRoutes()
	target, ok := routes[prefix]
	require.True(t, ok, "%s is absent from ServiceRoutes: the route matrix gap is open again", prefix)
	require.Equal(t, backing.URL, target, "%s should be served by the service behind %s", prefix, envVar)

	r := newTestRouter(t, []Route{{Prefix: prefix, TargetURL: target}})
	rec := doRequest(t, r, http.MethodGet, prefix+"/thing", nil)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, prefix+"/thing", backing.last(t).Path)
}

// --- X-User-ID identity propagation and spoofing ----------------------------

const routerTestSecret = "router-test-secret"

// gatewayChain wires the real JWT middleware in front of the proxy router, the
// way cmd/server does, so identity propagation is exercised end to end.
func gatewayChain(t *testing.T, routes []Route) http.Handler {
	t.Helper()
	prefixes := make([]string, 0, len(routes))
	for _, r := range routes {
		prefixes = append(prefixes, r.Prefix)
	}
	jwtMW := middleware.JWTAuth(middleware.JWTConfig{
		Secret:              routerTestSecret,
		PublicPath:          middleware.DefaultPublicPaths(),
		PrefixPath:          middleware.DefaultPrefixPaths(),
		ProtectedPrefixPath: prefixes,
	})
	return jwtMW(newTestRouter(t, routes))
}

func signRouterToken(t *testing.T, claims middleware.JWTClaims) string {
	t.Helper()
	tokenStr, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(routerTestSecret))
	require.NoError(t, err)
	return tokenStr
}

func TestProxy_SetsXUserIDFromSubjectClaim(t *testing.T) {
	files := newUpstream(t, "file-service")
	h := gatewayChain(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	token := signRouterToken(t, middleware.JWTClaims{
		UserID: "ignored-when-subject-present",
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-from-sub",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "user-from-sub", files.last(t).Header.Get("X-User-ID"))
}

func TestProxy_FallsBackToUserIDClaimWhenSubjectEmpty(t *testing.T) {
	files := newUpstream(t, "file-service")
	h := gatewayChain(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	token := signRouterToken(t, middleware.JWTClaims{
		UserID: "user-from-custom-claim",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "user-from-custom-claim", files.last(t).Header.Get("X-User-ID"))
}

// Authorization negative: on an authenticated route, a client-supplied
// X-User-ID is overwritten by the identity in the token, so user A cannot
// impersonate user B by adding a header.
func TestProxy_ClientSuppliedXUserIDIsOverwrittenOnAuthenticatedRoute(t *testing.T) {
	files := newUpstream(t, "file-service")
	h := gatewayChain(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	token := signRouterToken(t, middleware.JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-a",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("X-User-ID", "victim-user-b")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	got := files.last(t).Header
	assert.Equal(t, "user-a", got.Get("X-User-ID"))
	assert.Equal(t, []string{"user-a"}, got.Values("X-User-ID"), "no spoofed value may be left alongside the claim value")
}

// SECURITY FINDING (documents current behavior, does not fix it).
//
// The proxy director only *sets* X-User-ID when JWT claims are present in the
// request context. On a public path the JWT middleware never populates claims,
// so a client-supplied X-User-ID passes through untouched to the backing
// service. docs/api-route-matrix.md notes that some services trust that header
// blindly. The gateway never strips the inbound header.
//
// This test asserts today's behavior on purpose so the exposure is visible;
// TestProxy_SpoofedXUserIDShouldBeStrippedOnPublicPath below is the skipped
// test describing the behavior we want instead.
func TestProxy_SpoofedXUserIDSurvivesOnPublicPath_SECURITY_FINDING(t *testing.T) {
	auth := newUpstream(t, "auth-service")
	h := gatewayChain(t, []Route{{Prefix: "/api/v1/auth", TargetURL: auth.URL}})

	req := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", nil)
	req.Header.Set("X-User-ID", "spoofed-admin")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "spoofed-admin", auth.last(t).Header.Get("X-User-ID"),
		"current behavior: the gateway forwards the client's X-User-ID verbatim on unauthenticated routes")
}

func TestProxy_SpoofedXUserIDShouldBeStrippedOnPublicPath(t *testing.T) {
	t.Skip("DEFECT: the gateway does not strip an inbound X-User-ID before proxying. " +
		"On public paths (and on /socket.io) a client can inject any identity. " +
		"Fixing it means deleting the header in newProxyHandler's director when no " +
		"claims are present - a production change, out of scope for this coverage PR.")

	auth := newUpstream(t, "auth-service")
	h := gatewayChain(t, []Route{{Prefix: "/api/v1/auth", TargetURL: auth.URL}})

	req := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", nil)
	req.Header.Set("X-User-ID", "spoofed-admin")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Empty(t, auth.last(t).Header.Get("X-User-ID"))
}

// Same exposure, reached with a *valid* token that carries neither "sub" nor
// "user_id": the director finds claims but no user id, so it leaves the
// client's header in place.
func TestProxy_SpoofedXUserIDSurvivesWhenClaimsCarryNoUserID_SECURITY_FINDING(t *testing.T) {
	files := newUpstream(t, "file-service")
	h := gatewayChain(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	token := signRouterToken(t, middleware.JWTClaims{
		Email: "otter@otterworks.dev",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("X-User-ID", "spoofed-admin")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "spoofed-admin", files.last(t).Header.Get("X-User-ID"),
		"current behavior: an anonymous-but-valid token leaves the spoofed header intact")
}

// /socket.io is in DefaultPrefixPaths, so it skips JWT validation entirely and
// is proxied to the collab service with whatever identity header the client
// sent.
func TestProxy_SpoofedXUserIDSurvivesOnSocketIOPrefix_SECURITY_FINDING(t *testing.T) {
	collab := newUpstream(t, "collab-service")
	h := gatewayChain(t, []Route{{Prefix: "/socket.io", TargetURL: collab.URL}})

	req := httptest.NewRequest(http.MethodGet, "/socket.io/?EIO=4&transport=polling", nil)
	req.Header.Set("X-User-ID", "spoofed-admin")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "spoofed-admin", collab.last(t).Header.Get("X-User-ID"))
}

func TestProxy_UnauthenticatedRequestToProtectedRouteNeverReachesUpstream(t *testing.T) {
	files := newUpstream(t, "file-service")
	h := gatewayChain(t, []Route{{Prefix: "/api/v1/files", TargetURL: files.URL}})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/x", nil)
	req.Header.Set("X-User-ID", "spoofed-admin")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
	assert.Equal(t, 0, files.count(), "an unauthenticated request must not be proxied at all")
}
