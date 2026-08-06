package middleware

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/rs/zerolog"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// withGlobalLogLevel pins the zerolog global level for one test and restores whatever it
// was afterwards, so neither the ambient environment nor another test can change the
// outcome (SetLogLevel mutates process-global state).
func withGlobalLogLevel(t *testing.T, level zerolog.Level) {
	t.Helper()
	previous := zerolog.GlobalLevel()
	t.Cleanup(func() { zerolog.SetGlobalLevel(previous) })
	zerolog.SetGlobalLevel(level)
}

// runLogger sends one request through the Logger middleware and returns the decoded log
// line the middleware emitted.
func runLogger(t *testing.T, req *http.Request, handler http.HandlerFunc) map[string]any {
	t.Helper()
	withGlobalLogLevel(t, zerolog.TraceLevel)

	var buf strings.Builder
	logger := zerolog.New(&buf)

	rec := httptest.NewRecorder()
	Logger(logger)(handler).ServeHTTP(rec, req)

	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	require.Len(t, lines, 1, "exactly one log line per request, got: %q", buf.String())

	var entry map[string]any
	require.NoError(t, json.Unmarshal([]byte(lines[0]), &entry), "log line: %s", lines[0])
	return entry
}

func TestLogger_LogsRequestDetails(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/v1/files/upload?folder=root", strings.NewReader("body"))
	req.Header.Set("User-Agent", "otter-test/1.0")
	req.RemoteAddr = "203.0.113.7:54321"

	entry := runLogger(t, req, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte("hello"))
	})

	assert.Equal(t, "request completed", entry["message"])
	assert.Equal(t, http.MethodPost, entry["method"])
	assert.Equal(t, "/api/v1/files/upload", entry["path"])
	assert.Equal(t, "folder=root", entry["query"])
	assert.Equal(t, float64(http.StatusCreated), entry["status"])
	assert.Equal(t, float64(len("hello")), entry["bytes"])
	assert.Equal(t, "203.0.113.7:54321", entry["remote_addr"])
	assert.Equal(t, "otter-test/1.0", entry["user_agent"])
	assert.Equal(t, "HTTP/1.1", entry["protocol"])
	assert.Contains(t, entry, "latency_ms")
}

func TestLogger_SeverityBoundaryTrioAroundClientErrors(t *testing.T) {
	// The middleware switches severity at 400 and again at 500.
	cases := []struct {
		status    int
		wantLevel string
	}{
		{http.StatusOK, "info"},
		{http.StatusPermanentRedirect, "info"},
		{399, "info"},                             // limit-1
		{http.StatusBadRequest, "warn"},           // limit
		{401, "warn"},                             // limit+1
		{499, "warn"},                             // limit-1
		{http.StatusInternalServerError, "error"}, // limit
		{501, "error"},                            // limit+1
		{599, "error"},
	}

	for _, tc := range cases {
		t.Run(strconv.Itoa(tc.status), func(t *testing.T) {
			entry := runLogger(t, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
				func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(tc.status) })

			assert.Equal(t, tc.wantLevel, entry["level"])
			assert.Equal(t, float64(tc.status), entry["status"])
		})
	}
}

func TestLogger_HandlerThatWritesNothingLogsStatusZero(t *testing.T) {
	// Nothing calls WriteHeader, so the wrapped writer never learns a status. Pinned as
	// current behaviour: it is logged as 0 at info level rather than as an implicit 200.
	entry := runLogger(t, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
		func(w http.ResponseWriter, r *http.Request) {})

	assert.Equal(t, float64(0), entry["status"])
	assert.Equal(t, float64(0), entry["bytes"])
	assert.Equal(t, "info", entry["level"])
}

func TestLogger_ImplicitStatusFromBareWriteIsLoggedAs200(t *testing.T) {
	entry := runLogger(t, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
		func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("ok")) })

	assert.Equal(t, float64(http.StatusOK), entry["status"])
	assert.Equal(t, float64(2), entry["bytes"])
}

func TestLogger_IncludesRequestIDWhenRequestIDMiddlewareRanFirst(t *testing.T) {
	withGlobalLogLevel(t, zerolog.TraceLevel)

	var buf strings.Builder
	logger := zerolog.New(&buf)

	handler := RequestID(Logger(logger)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files", nil)
	req.Header.Set("X-Request-ID", "req-abc-123")
	handler.ServeHTTP(httptest.NewRecorder(), req)

	var entry map[string]any
	require.NoError(t, json.Unmarshal([]byte(strings.TrimSpace(buf.String())), &entry))
	assert.Equal(t, "req-abc-123", entry["request_id"])
}

func TestLogger_LogsEmptyRequestIDWhenMiddlewareDidNotRun(t *testing.T) {
	entry := runLogger(t, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
		func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) })

	assert.Equal(t, "", entry["request_id"],
		"a missing request id must not panic or omit the field")
}

func TestLogger_QueryStringIsLoggedButBodyIsNot(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login?redirect=%2Fhome",
		strings.NewReader(`{"password":"hunter2"}`))

	entry := runLogger(t, req, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	assert.Equal(t, "redirect=%2Fhome", entry["query"], "the raw, still-encoded query is logged")
	assert.NotContains(t, entry, "body")
	for _, value := range entry {
		if text, ok := value.(string); ok {
			assert.NotContains(t, text, "hunter2", "request bodies must never be logged")
		}
	}
}

func TestLogger_SuppressedByGlobalLevel(t *testing.T) {
	withGlobalLogLevel(t, zerolog.ErrorLevel)

	var buf strings.Builder
	logger := zerolog.New(&buf)

	Logger(logger)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})).ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/api/v1/files", nil))

	assert.Empty(t, buf.String(), "an info-level request line is dropped when the global level is error")
}

func TestLogger_PassesResponseThroughUnchanged(t *testing.T) {
	withGlobalLogLevel(t, zerolog.TraceLevel)

	var buf strings.Builder
	rec := httptest.NewRecorder()

	Logger(zerolog.New(&buf))(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Custom", "kept")
		w.WriteHeader(http.StatusTeapot)
		_, _ = w.Write([]byte("payload"))
	})).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil))

	assert.Equal(t, http.StatusTeapot, rec.Code)
	assert.Equal(t, "kept", rec.Header().Get("X-Custom"))
	assert.Equal(t, "payload", rec.Body.String())
}

func TestSetLogLevel(t *testing.T) {
	cases := map[string]zerolog.Level{
		"debug":       zerolog.DebugLevel,
		"info":        zerolog.InfoLevel,
		"warn":        zerolog.WarnLevel,
		"error":       zerolog.ErrorLevel,
		"":            zerolog.InfoLevel, // unknown values fall back to info
		"trace":       zerolog.InfoLevel, // zerolog knows "trace"; SetLogLevel does not
		"INFO":        zerolog.InfoLevel, // matching is case-sensitive, so this is the fallback
		"not-a-level": zerolog.InfoLevel,
	}

	for input, want := range cases {
		t.Run("level_"+input, func(t *testing.T) {
			withGlobalLogLevel(t, zerolog.TraceLevel)
			SetLogLevel(input)
			assert.Equal(t, want, zerolog.GlobalLevel())
		})
	}
}
