package middleware

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"

	"github.com/rs/zerolog"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// captureLogs installs a buffer-backed logger and forces the (process-global)
// zerolog level low enough that every event is emitted, restoring the previous
// global level afterwards so test order never matters.
func captureLogs(t *testing.T) (*bytes.Buffer, zerolog.Logger) {
	t.Helper()
	previous := zerolog.GlobalLevel()
	t.Cleanup(func() { zerolog.SetGlobalLevel(previous) })
	zerolog.SetGlobalLevel(zerolog.TraceLevel)

	buf := &bytes.Buffer{}
	return buf, zerolog.New(buf)
}

// logLine decodes the single JSON log line the middleware emitted.
func logLine(t *testing.T, buf *bytes.Buffer) map[string]interface{} {
	t.Helper()
	raw := bytes.TrimSpace(buf.Bytes())
	require.NotEmpty(t, raw, "expected exactly one log line, got none")
	require.NotContains(t, string(raw), "\n", "expected exactly one log line")

	var fields map[string]interface{}
	require.NoError(t, json.Unmarshal(raw, &fields))
	return fields
}

func serveLogged(t *testing.T, buf *bytes.Buffer, logger zerolog.Logger, req *http.Request, inner http.HandlerFunc) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	Logger(logger)(inner).ServeHTTP(rec, req)
	return rec
}

// --- positive: fields -------------------------------------------------------

func TestLogger_LogsRequestFields(t *testing.T) {
	buf, logger := captureLogs(t)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/files/upload?folder=root&dry=1", nil)
	req.Header.Set("User-Agent", "otter-agent/1.0")
	req.RemoteAddr = "203.0.113.7:54321"

	rec := serveLogged(t, buf, logger, req, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"id":"f-1"}`))
	})
	require.Equal(t, http.StatusCreated, rec.Code)

	fields := logLine(t, buf)
	assert.Equal(t, "request completed", fields["message"])
	assert.Equal(t, "POST", fields["method"])
	assert.Equal(t, "/api/v1/files/upload", fields["path"])
	assert.Equal(t, "folder=root&dry=1", fields["query"])
	assert.Equal(t, float64(http.StatusCreated), fields["status"])
	assert.Equal(t, float64(len(`{"id":"f-1"}`)), fields["bytes"])
	assert.Equal(t, "203.0.113.7:54321", fields["remote_addr"])
	assert.Equal(t, "otter-agent/1.0", fields["user_agent"])
	assert.Equal(t, "HTTP/1.1", fields["protocol"])
	assert.Contains(t, fields, "latency_ms")
	assert.GreaterOrEqual(t, fields["latency_ms"], float64(0))
}

func TestLogger_IncludesRequestIDWhenPresent(t *testing.T) {
	buf, logger := captureLogs(t)

	// RequestID must run first so the id is in the context the logger reads.
	chain := RequestID(Logger(logger)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files", nil)
	req.Header.Set("X-Request-ID", "req-abc-123")
	chain.ServeHTTP(httptest.NewRecorder(), req)

	assert.Equal(t, "req-abc-123", logLine(t, buf)["request_id"])
}

func TestLogger_RequestIDIsEmptyWhenMiddlewareNotInChain(t *testing.T) {
	buf, logger := captureLogs(t)

	serveLogged(t, buf, logger, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
		func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) })

	assert.Equal(t, "", logLine(t, buf)["request_id"])
}

func TestLogger_EmptyQueryIsLoggedAsEmptyString(t *testing.T) {
	buf, logger := captureLogs(t)

	serveLogged(t, buf, logger, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
		func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) })

	assert.Equal(t, "", logLine(t, buf)["query"])
}

func TestLogger_PassesTheRequestThroughUnchanged(t *testing.T) {
	buf, logger := captureLogs(t)

	var gotMethod, gotPath string
	rec := serveLogged(t, buf, logger, httptest.NewRequest(http.MethodPut, "/api/v1/documents/7", nil),
		func(w http.ResponseWriter, r *http.Request) {
			gotMethod, gotPath = r.Method, r.URL.Path
			w.Header().Set("X-Inner", "yes")
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte("body"))
		})

	assert.Equal(t, http.MethodPut, gotMethod)
	assert.Equal(t, "/api/v1/documents/7", gotPath)
	assert.Equal(t, http.StatusAccepted, rec.Code)
	assert.Equal(t, "yes", rec.Header().Get("X-Inner"))
	assert.Equal(t, "body", rec.Body.String())
}

// --- boundary: the 400 and 500 level thresholds -----------------------------

func TestLogger_LevelBoundariesAroundStatus400And500(t *testing.T) {
	cases := []struct {
		status int
		level  string
	}{
		{http.StatusOK, "info"},
		{http.StatusFound, "info"},
		{399, "info"},  // limit-1 for the warn threshold
		{400, "warn"},  // limit
		{401, "warn"},  // limit+1
		{499, "warn"},  // limit-1 for the error threshold
		{500, "error"}, // limit
		{501, "error"}, // limit+1
		{599, "error"},
	}

	for _, tc := range cases {
		t.Run("status_"+strconv.Itoa(tc.status), func(t *testing.T) {
			buf, logger := captureLogs(t)
			status := tc.status
			serveLogged(t, buf, logger, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
				func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status) })

			fields := logLine(t, buf)
			assert.Equal(t, tc.level, fields["level"])
			assert.Equal(t, float64(tc.status), fields["status"])
		})
	}
}

// A handler that writes a body without calling WriteHeader is a 200 as far as
// the wrapper is concerned.
func TestLogger_ImplicitOKIsLoggedAs200(t *testing.T) {
	buf, logger := captureLogs(t)

	serveLogged(t, buf, logger, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
		func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("hi")) })

	fields := logLine(t, buf)
	assert.Equal(t, float64(http.StatusOK), fields["status"])
	assert.Equal(t, float64(2), fields["bytes"])
	assert.Equal(t, "info", fields["level"])
}

// A handler that writes nothing at all leaves the wrapper's status at 0, which
// the switch treats as the default (info) branch. Pinned as current behavior.
func TestLogger_HandlerThatWritesNothingIsLoggedAsStatusZero(t *testing.T) {
	buf, logger := captureLogs(t)

	serveLogged(t, buf, logger, httptest.NewRequest(http.MethodGet, "/api/v1/files", nil),
		func(w http.ResponseWriter, r *http.Request) {})

	fields := logLine(t, buf)
	assert.Equal(t, float64(0), fields["status"])
	assert.Equal(t, float64(0), fields["bytes"])
	assert.Equal(t, "info", fields["level"])
}

func TestLogger_OneLinePerRequest(t *testing.T) {
	buf, logger := captureLogs(t)
	h := Logger(logger)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	for i := 0; i < 3; i++ {
		h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/api/v1/files", nil))
	}

	assert.Len(t, bytes.Split(bytes.TrimSpace(buf.Bytes()), []byte("\n")), 3)
}

// --- SetLogLevel ------------------------------------------------------------

func TestSetLogLevel(t *testing.T) {
	cases := []struct {
		input    string
		expected zerolog.Level
	}{
		{"debug", zerolog.DebugLevel},
		{"info", zerolog.InfoLevel},
		{"warn", zerolog.WarnLevel},
		{"error", zerolog.ErrorLevel},
		// Anything unrecognised falls back to info.
		{"", zerolog.InfoLevel},
		{"INFO", zerolog.InfoLevel},
		{"Debug", zerolog.InfoLevel},
		{"trace", zerolog.InfoLevel},
		{"fatal", zerolog.InfoLevel},
		{"panic", zerolog.InfoLevel},
		{"nonsense", zerolog.InfoLevel},
	}

	for _, tc := range cases {
		t.Run("level="+tc.input, func(t *testing.T) {
			previous := zerolog.GlobalLevel()
			t.Cleanup(func() { zerolog.SetGlobalLevel(previous) })

			SetLogLevel(tc.input)

			assert.Equal(t, tc.expected, zerolog.GlobalLevel())
		})
	}
}

// The global level actually suppresses lower-severity request logs.
func TestSetLogLevel_SuppressesLowerSeverityRequestLogs(t *testing.T) {
	previous := zerolog.GlobalLevel()
	t.Cleanup(func() { zerolog.SetGlobalLevel(previous) })

	buf := &bytes.Buffer{}
	logger := zerolog.New(buf)
	h := Logger(logger)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	SetLogLevel("error")
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/api/v1/files", nil))
	assert.Empty(t, buf.String(), "an info-level request log must be dropped at error level")

	SetLogLevel("info")
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/api/v1/files", nil))
	assert.NotEmpty(t, buf.String())
}
