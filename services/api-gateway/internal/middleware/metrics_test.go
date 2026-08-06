package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// The metric vectors are package-level, so every assertion here is written as
// a delta around the action under test and each test uses its own HTTP method
// as a label, which keeps the tests independent of execution order.

func requestCount(method, path, status string) float64 {
	return testutil.ToFloat64(httpRequestsTotal.WithLabelValues(method, path, status))
}

func serveMetrics(t *testing.T, method, target string, inner http.HandlerFunc) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	Metrics(inner).ServeHTTP(rec, httptest.NewRequest(method, target, nil))
	return rec
}

func okHandler(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) }

// --- counter ----------------------------------------------------------------

func TestMetrics_CountsRequestByMethodPathAndStatus(t *testing.T) {
	const method = "REPORT" // unique to this test

	before := requestCount(method, "/api/v1/files", "200")
	serveMetrics(t, method, "/api/v1/files/abc", okHandler)

	assert.Equal(t, before+1, requestCount(method, "/api/v1/files", "200"))
}

func TestMetrics_CountsEachStatusSeparately(t *testing.T) {
	const method = "MKCOL"

	statuses := []int{http.StatusOK, http.StatusNotFound, http.StatusInternalServerError}
	before := map[int]float64{}
	for _, s := range statuses {
		before[s] = requestCount(method, "/api/v1/files", statusString(s))
	}

	for _, s := range statuses {
		s := s
		serveMetrics(t, method, "/api/v1/files", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(s) })
	}

	for _, s := range statuses {
		assert.Equal(t, before[s]+1, requestCount(method, "/api/v1/files", statusString(s)),
			"status %d should have its own series", s)
	}
}

func TestMetrics_RepeatedRequestsAccumulate(t *testing.T) {
	const method = "PROPFIND"

	before := requestCount(method, "/api/v1/search", "200")
	for i := 0; i < 3; i++ {
		serveMetrics(t, method, "/api/v1/search?q=otter", okHandler)
	}

	assert.Equal(t, before+3, requestCount(method, "/api/v1/search", "200"))
}

// A handler that writes nothing leaves the wrapped writer's status at 0, which
// is recorded verbatim as the label value "0". Pinned as current behavior.
func TestMetrics_HandlerThatWritesNothingIsCountedAsStatusZero(t *testing.T) {
	const method = "LOCK"

	before := requestCount(method, "/api/v1/files", "0")
	serveMetrics(t, method, "/api/v1/files", func(w http.ResponseWriter, r *http.Request) {})

	assert.Equal(t, before+1, requestCount(method, "/api/v1/files", "0"))
}

func TestMetrics_UnknownPathIsCountedUnderOther(t *testing.T) {
	const method = "UNLOCK"

	before := requestCount(method, "other", "200")
	serveMetrics(t, method, "/nothing/here", okHandler)

	assert.Equal(t, before+1, requestCount(method, "other", "200"))
}

// --- gauge ------------------------------------------------------------------

func TestMetrics_ActiveConnectionsGaugeRisesAndFalls(t *testing.T) {
	baseline := testutil.ToFloat64(httpActiveConnections)

	var during float64
	serveMetrics(t, "COPY", "/api/v1/files", func(w http.ResponseWriter, r *http.Request) {
		during = testutil.ToFloat64(httpActiveConnections)
		w.WriteHeader(http.StatusOK)
	})

	assert.Equal(t, baseline+1, during, "the gauge must be incremented while the handler runs")
	assert.Equal(t, baseline, testutil.ToFloat64(httpActiveConnections), "and decremented afterwards")
}

// The decrement is deferred, so a panicking handler must not leak a
// permanently-elevated gauge.
func TestMetrics_ActiveConnectionsGaugeIsReleasedOnPanic(t *testing.T) {
	baseline := testutil.ToFloat64(httpActiveConnections)

	require.Panics(t, func() {
		serveMetrics(t, "MOVE", "/api/v1/files", func(w http.ResponseWriter, r *http.Request) {
			panic("boom")
		})
	})

	assert.Equal(t, baseline, testutil.ToFloat64(httpActiveConnections))
}

// --- pass-through -----------------------------------------------------------

func TestMetrics_PassesResponseThrough(t *testing.T) {
	rec := serveMetrics(t, http.MethodGet, "/api/v1/files/1", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Inner", "yes")
		w.WriteHeader(http.StatusTeapot)
		_, _ = w.Write([]byte("short and stout"))
	})

	assert.Equal(t, http.StatusTeapot, rec.Code)
	assert.Equal(t, "yes", rec.Header().Get("X-Inner"))
	assert.Equal(t, "short and stout", rec.Body.String())
}

// --- normalizePath ----------------------------------------------------------

func TestNormalizePath_MappedPrefixes(t *testing.T) {
	cases := []struct {
		path     string
		expected string
	}{
		{"/api/v1/auth", "/api/v1/auth"},
		{"/api/v1/auth/login", "/api/v1/auth"},
		{"/api/v1/files", "/api/v1/files"},
		{"/api/v1/files/abc/download", "/api/v1/files"},
		{"/api/v1/documents", "/api/v1/documents"},
		{"/api/v1/documents/1/versions", "/api/v1/documents"},
		{"/api/v1/collab", "/api/v1/collab"},
		{"/api/v1/notifications", "/api/v1/notifications"},
		{"/api/v1/search", "/api/v1/search"},
		{"/api/v1/analytics", "/api/v1/analytics"},
		{"/api/v1/admin", "/api/v1/admin"},
		{"/api/v1/audit", "/api/v1/audit"},
	}

	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			assert.Equal(t, tc.expected, normalizePath(tc.path))
		})
	}
}

func TestNormalizePath_UnmappedPathsCollapseToOther(t *testing.T) {
	for _, path := range []string{"", "/", "/health", "/metrics", "/api", "/api/v1", "/api/v2/files", "/random"} {
		t.Run("path="+path, func(t *testing.T) {
			assert.Equal(t, "other", normalizePath(path))
		})
	}
}

// Boundary: matching is a raw prefix comparison, so one character short of a
// known prefix is "other" and one character past it still matches.
func TestNormalizePath_PrefixBoundaries(t *testing.T) {
	assert.Equal(t, "other", normalizePath("/api/v1/aut"), "one char short of /api/v1/auth")
	assert.Equal(t, "/api/v1/auth", normalizePath("/api/v1/auth"), "exact prefix")
	assert.Equal(t, "/api/v1/auth", normalizePath("/api/v1/auth/"), "one char past the prefix")
}

// FINDING (documents current behavior): the match is not segment-aware, so a
// sibling route whose name merely starts with a known prefix is folded into
// that prefix's series.
func TestNormalizePath_SiblingSegmentIsFoldedIntoThePrefix_FINDING(t *testing.T) {
	assert.Equal(t, "/api/v1/auth", normalizePath("/api/v1/authorize"))
	assert.Equal(t, "/api/v1/files", normalizePath("/api/v1/filesystem"))
}

// FINDING (documents current behavior): six prefixes that the gateway really
// routes (config.ServiceRoutes) have no case in normalizePath, so all their
// traffic is reported as "other" - a blind spot in per-service dashboards and
// alerts. Adding the cases is a production change, out of scope here.
func TestNormalizePath_RoutedPrefixesMissingFromMetricLabels_FINDING(t *testing.T) {
	unlabelled := []string{
		"/api/v1/folders",
		"/api/v1/templates",
		"/api/v1/preferences",
		"/api/v1/reports",
		"/api/v1/settings",
		"/socket.io",
	}

	for _, path := range unlabelled {
		t.Run(path, func(t *testing.T) {
			assert.Equal(t, "other", normalizePath(path),
				"current behavior: %s is routed but has no metrics label of its own", path)
		})
	}
}

func TestNormalizePath_RoutedPrefixesShouldHaveTheirOwnMetricLabel(t *testing.T) {
	t.Skip("DEFECT: normalizePath has no case for /api/v1/folders, /api/v1/templates, " +
		"/api/v1/preferences, /api/v1/reports, /api/v1/settings or /socket.io, so six routed " +
		"prefixes share the 'other' series. Adding the cases is a production change to " +
		"internal/middleware/metrics.go, out of scope for this coverage PR.")

	assert.Equal(t, "/api/v1/reports", normalizePath("/api/v1/reports/42"))
}

func statusString(status int) string {
	switch status {
	case http.StatusOK:
		return "200"
	case http.StatusNotFound:
		return "404"
	case http.StatusInternalServerError:
		return "500"
	default:
		return "unknown"
	}
}
