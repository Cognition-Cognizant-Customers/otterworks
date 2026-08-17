package middleware

import (
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	httpRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "api_gateway",
			Name:      "http_requests_total",
			Help:      "Total number of HTTP requests.",
		},
		[]string{"method", "path", "status"},
	)

	httpRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "api_gateway",
			Name:      "http_request_duration_seconds",
			Help:      "HTTP request latency in seconds.",
			Buckets:   prometheus.DefBuckets,
		},
		[]string{"method", "path"},
	)

	httpActiveConnections = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "api_gateway",
			Name:      "http_active_connections",
			Help:      "Number of active HTTP connections.",
		},
	)
)

// Metrics returns HTTP middleware that records Prometheus metrics for each request.
func Metrics(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)

		httpActiveConnections.Inc()
		defer httpActiveConnections.Dec()

		next.ServeHTTP(ww, r)

		duration := time.Since(start).Seconds()
		status := strconv.Itoa(ww.Status())
		path := normalizePath(r.URL.Path)

		httpRequestsTotal.WithLabelValues(r.Method, path, status).Inc()
		httpRequestDuration.WithLabelValues(r.Method, path).Observe(duration)
	})
}

// metricPathPrefixes are the top-level route prefixes used to group metrics,
// checked in order.
var metricPathPrefixes = []string{
	"/api/v1/auth",
	"/api/v1/files",
	"/api/v1/documents",
	"/api/v1/collab",
	"/api/v1/notifications",
	"/api/v1/search",
	"/api/v1/analytics",
	"/api/v1/admin",
	"/api/v1/audit",
}

// normalizePath reduces cardinality by collapsing path parameters.
func normalizePath(path string) string {
	for _, prefix := range metricPathPrefixes {
		if strings.HasPrefix(path, prefix) {
			return prefix
		}
	}
	return "other"
}
