package middleware

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// The metric vectors are package-level and shared with every other test in this package,
// so each test uses its own synthetic HTTP method as a label value and asserts on the
// delta rather than the absolute count. That keeps the assertions independent of test
// order and of how many requests other tests happened to record.
func requestCount(t *testing.T, method, path, status string) float64 {
	t.Helper()
	counter, err := httpRequestsTotal.GetMetricWithLabelValues(method, path, status)
	require.NoError(t, err)
	return testutil.ToFloat64(counter)
}

func serveWithMetrics(t *testing.T, method, path string, handler http.HandlerFunc) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	Metrics(handler).ServeHTTP(rec, httptest.NewRequest(method, path, nil))
	return rec
}

func TestMetrics_CountsRequestPerMethodPathAndStatus(t *testing.T) {
	const method = "OTTERCOUNT"
	before := requestCount(t, method, "/api/v1/files", "201")

	serveWithMetrics(t, method, "/api/v1/files/upload", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
	})

	assert.Equal(t, before+1, requestCount(t, method, "/api/v1/files", "201"),
		"the counter is keyed by the normalised path, not the raw one")
}

func TestMetrics_RecordsEachStatusSeparately(t *testing.T) {
	const method = "OTTERSTATUS"

	statuses := []int{http.StatusOK, http.StatusNotFound, http.StatusInternalServerError}
	before := make(map[int]float64, len(statuses))
	for _, status := range statuses {
		before[status] = requestCount(t, method, "/api/v1/admin", strconv.Itoa(status))
	}

	for _, status := range statuses {
		status := status
		serveWithMetrics(t, method, "/api/v1/admin/users", func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(status)
		})
	}

	for _, status := range statuses {
		assert.Equal(t, before[status]+1, requestCount(t, method, "/api/v1/admin", strconv.Itoa(status)),
			"status %d", status)
	}
}

func TestMetrics_HandlerWithoutExplicitStatusIsRecordedAsZero(t *testing.T) {
	// Pinned: a handler that writes nothing yields a "0" status label rather than "200",
	// so silent handlers show up as a distinct series in Prometheus.
	const method = "OTTERSILENT"
	before := requestCount(t, method, "other", "0")

	serveWithMetrics(t, method, "/does-not-matter", func(w http.ResponseWriter, r *http.Request) {})

	assert.Equal(t, before+1, requestCount(t, method, "other", "0"))
}

func TestMetrics_CreatesOneLatencySeriesPerMethodAndPath(t *testing.T) {
	const method = "OTTERLATENCY"
	before := testutil.CollectAndCount(httpRequestDuration)

	serveWithMetrics(t, method, "/api/v1/search?q=otter", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	after := testutil.CollectAndCount(httpRequestDuration)
	require.Equal(t, before+1, after, "a new (method, path) latency series is created")

	// A second request with the same labels observes into the existing series rather than
	// creating a new one, and the status label does not appear on the latency histogram.
	serveWithMetrics(t, method, "/api/v1/search/suggest", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	})
	assert.Equal(t, after, testutil.CollectAndCount(httpRequestDuration))
}

func TestMetrics_ActiveConnectionsGaugeReturnsToItsPreviousValue(t *testing.T) {
	before := testutil.ToFloat64(httpActiveConnections)

	var during float64
	serveWithMetrics(t, "OTTERGAUGE", "/api/v1/files", func(w http.ResponseWriter, r *http.Request) {
		during = testutil.ToFloat64(httpActiveConnections)
		w.WriteHeader(http.StatusOK)
	})

	assert.Equal(t, before+1, during, "the gauge is incremented while the handler runs")
	assert.Equal(t, before, testutil.ToFloat64(httpActiveConnections),
		"the gauge must be decremented again once the request completes")
}

func TestMetrics_GaugeIsDecrementedEvenWhenTheHandlerPanics(t *testing.T) {
	before := testutil.ToFloat64(httpActiveConnections)

	assert.Panics(t, func() {
		serveWithMetrics(t, "OTTERPANIC", "/api/v1/files", func(w http.ResponseWriter, r *http.Request) {
			panic("boom")
		})
	})

	assert.Equal(t, before, testutil.ToFloat64(httpActiveConnections),
		"a panicking handler must not leak an active connection (the defer covers it)")
}

func TestMetrics_PassesResponseThroughUnchanged(t *testing.T) {
	rec := serveWithMetrics(t, "OTTERPASS", "/api/v1/files", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Custom", "kept")
		w.WriteHeader(http.StatusTeapot)
		_, _ = w.Write([]byte("payload"))
	})

	assert.Equal(t, http.StatusTeapot, rec.Code)
	assert.Equal(t, "kept", rec.Header().Get("X-Custom"))
	assert.Equal(t, "payload", rec.Body.String())
}

func TestMetrics_ConcurrentRequestsAreAllCounted(t *testing.T) {
	const method = "OTTERCONCURRENT"
	const workers = 50
	before := requestCount(t, method, "/api/v1/documents", "200")
	gaugeBefore := testutil.ToFloat64(httpActiveConnections)

	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rec := httptest.NewRecorder()
			Metrics(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusOK)
			})).ServeHTTP(rec, httptest.NewRequest(method, "/api/v1/documents/x", nil))
		}()
	}
	wg.Wait()

	assert.Equal(t, before+workers, requestCount(t, method, "/api/v1/documents", "200"),
		"no counter increments may be lost under concurrency")
	assert.Equal(t, gaugeBefore, testutil.ToFloat64(httpActiveConnections),
		"the active-connection gauge must balance out")
}

func TestNormalizePath_KnownPrefixesCollapseToTheirRoute(t *testing.T) {
	cases := map[string]string{
		"/api/v1/auth":                   "/api/v1/auth",
		"/api/v1/auth/login":             "/api/v1/auth",
		"/api/v1/files/abc/download":     "/api/v1/files",
		"/api/v1/documents/abc/versions": "/api/v1/documents",
		"/api/v1/collab/documents":       "/api/v1/collab",
		"/api/v1/notifications/unread":   "/api/v1/notifications",
		"/api/v1/search/suggest":         "/api/v1/search",
		"/api/v1/analytics/events":       "/api/v1/analytics",
		"/api/v1/admin/users/1":          "/api/v1/admin",
		"/api/v1/audit/events/1/history": "/api/v1/audit",
	}

	for path, want := range cases {
		t.Run(path, func(t *testing.T) {
			assert.Equal(t, want, normalizePath(path))
		})
	}
}

func TestNormalizePath_UnknownPathsCollapseToOther(t *testing.T) {
	for _, path := range []string{
		"",
		"/",
		"/health",
		"/metrics",
		"/api",
		"/api/v1",
		"/api/v1/aut", // one character short of the /api/v1/auth prefix
		"/api/v2/files",
		"/API/V1/FILES", // matching is case-sensitive
	} {
		t.Run(path, func(t *testing.T) {
			assert.Equal(t, "other", normalizePath(path))
		})
	}
}

// FINDING (genuine, not planted): normalizePath knows nine prefixes, but
// config.ServiceRoutes routes fifteen. Traffic to /api/v1/folders, /api/v1/templates,
// /api/v1/preferences, /api/v1/reports, /api/v1/settings and /socket.io is therefore
// bucketed into the single "other" series and cannot be measured per service. Pinned as
// current behaviour, not fixed — normalizePath is production code.
func TestNormalizePath_RoutedPrefixesMissingFromMetrics_currentBehaviour(t *testing.T) {
	for _, path := range []string{
		"/api/v1/folders/abc",
		"/api/v1/templates/abc",
		"/api/v1/preferences/email",
		"/api/v1/reports/1/download",
		"/api/v1/settings",
		"/socket.io/?EIO=4",
	} {
		t.Run(path, func(t *testing.T) {
			assert.Equal(t, "other", normalizePath(path),
				"pinning today's behaviour: this routed prefix has no metrics bucket")
		})
	}
}

// FINDING (genuine, not planted): the prefix comparison is a raw substring check with no
// boundary test, so any path that merely *starts with* a known prefix is folded into it.
// /api/v1/authorised-partners is counted as /api/v1/auth traffic.
func TestNormalizePath_PrefixMatchHasNoSegmentBoundary_currentBehaviour(t *testing.T) {
	cases := map[string]string{
		"/api/v1/authorised-partners": "/api/v1/auth",
		"/api/v1/filesystem":          "/api/v1/files",
		"/api/v1/adminportal":         "/api/v1/admin",
	}

	for path, want := range cases {
		t.Run(path, func(t *testing.T) {
			assert.Equal(t, want, normalizePath(path),
				"pinning today's behaviour: an unrelated path is folded into a service bucket")
		})
	}
}

func TestNormalizePath_BoundaryTrioAroundTheShortestKnownPrefix(t *testing.T) {
	// "/api/v1/auth" is the shortest prefix normalizePath knows: one character shorter is
	// unknown, exactly the prefix matches, one character longer still matches.
	assert.Equal(t, "other", normalizePath("/api/v1/aut"))
	assert.Equal(t, "/api/v1/auth", normalizePath("/api/v1/auth"))
	assert.Equal(t, "/api/v1/auth", normalizePath("/api/v1/auth/"))
}
