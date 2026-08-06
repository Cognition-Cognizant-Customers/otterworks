package middleware

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRateLimiter_Allow(t *testing.T) {
	rl := NewRateLimiter(5)

	// First 5 requests should be allowed
	for i := 0; i < 5; i++ {
		assert.True(t, rl.Allow("192.168.1.1"), "request %d should be allowed", i+1)
	}

	// 6th request should be denied
	assert.False(t, rl.Allow("192.168.1.1"), "6th request should be denied")

	// Different IP should still be allowed
	assert.True(t, rl.Allow("192.168.1.2"), "different IP should be allowed")
}

func TestRateLimiter_TokenRefill(t *testing.T) {
	rl := NewRateLimiter(2)

	now := time.Now()
	rl.now = func() time.Time { return now }

	// Consume all tokens
	assert.True(t, rl.Allow("10.0.0.1"))
	assert.True(t, rl.Allow("10.0.0.1"))
	assert.False(t, rl.Allow("10.0.0.1"))

	// Advance time by 1 second - should refill 2 tokens
	rl.now = func() time.Time { return now.Add(1 * time.Second) }
	assert.True(t, rl.Allow("10.0.0.1"))
	assert.True(t, rl.Allow("10.0.0.1"))
	assert.False(t, rl.Allow("10.0.0.1"))
}

func TestRateLimiter_Handler(t *testing.T) {
	rl := NewRateLimiter(2)

	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// First 2 requests succeed
	for i := 0; i < 2; i++ {
		req := httptest.NewRequest(http.MethodGet, "/test", nil)
		req.RemoteAddr = "192.168.1.1:12345"
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		assert.Equal(t, http.StatusOK, rec.Code, "request %d should succeed", i+1)
	}

	// 3rd request gets rate limited
	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	req.RemoteAddr = "192.168.1.1:12345"
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusTooManyRequests, rec.Code)
	assert.Equal(t, "1", rec.Header().Get("Retry-After"))
}

func TestExtractIP(t *testing.T) {
	tests := []struct {
		name       string
		remoteAddr string
		expected   string
	}{
		{
			name:       "RemoteAddr with port",
			remoteAddr: "10.0.0.1:5678",
			expected:   "10.0.0.1",
		},
		{
			name:       "RemoteAddr without port",
			remoteAddr: "10.0.0.1",
			expected:   "10.0.0.1",
		},
		{
			name:       "Uses RemoteAddr set by chimw.RealIP",
			remoteAddr: "203.0.113.50:1234",
			expected:   "203.0.113.50",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/", nil)
			req.RemoteAddr = tt.remoteAddr
			result := extractIP(req)
			require.Equal(t, tt.expected, result)
		})
	}
}

// ---------------------------------------------------------------------------
// WP-04: rate limiter boundary, isolation, configuration and concurrency cases.
//
// Every case below drives the limiter from an injected clock (`RateLimiter.now`)
// so that no test depends on wall-clock time, sleeps, or execution order.
// Helper identifiers are prefixed `rl` to stay disjoint from other test files
// in this package.
// ---------------------------------------------------------------------------

// rlClock is a race-safe manually advanced clock.
type rlClock struct {
	mu sync.Mutex
	t  time.Time
}

func rlNewClock() *rlClock {
	return &rlClock{t: time.Date(2024, time.January, 1, 0, 0, 0, 0, time.UTC)}
}

func (c *rlClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *rlClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

// rlFrozen builds a limiter whose clock only moves when the test moves it.
func rlFrozen(rps int) (*RateLimiter, *rlClock) {
	rl := NewRateLimiter(rps)
	clk := rlNewClock()
	rl.now = clk.Now
	return rl, clk
}

// rlTokens reads the remaining tokens for a key, or -1 when no bucket exists.
func rlTokens(rl *RateLimiter, key string) float64 {
	rl.mu.Lock()
	defer rl.mu.Unlock()
	bucket, ok := rl.buckets[key]
	if !ok {
		return -1
	}
	return bucket.tokens
}

// rlDrain consumes n tokens, requiring every one of them to be admitted.
func rlDrain(t *testing.T, rl *RateLimiter, key string, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		require.True(t, rl.Allow(key), "token %d of %d should be admitted", i+1, n)
	}
}

func TestRateLimiter_LimitBoundary(t *testing.T) {
	const key = "198.51.100.7"

	for _, rps := range []int{1, 4, 100} {
		t.Run(fmt.Sprintf("rps=%d", rps), func(t *testing.T) {
			t.Run("limit-1 leaves capacity", func(t *testing.T) {
				rl, _ := rlFrozen(rps)
				rlDrain(t, rl, key, rps-1)
				if rps > 1 {
					assert.InDelta(t, 1.0, rlTokens(rl, key), 1e-9,
						"exactly one token must remain after rps-1 requests")
				} else {
					assert.Equal(t, -1.0, rlTokens(rl, key),
						"a bucket is only created on the first request")
				}
				assert.True(t, rl.Allow(key), "the rps-th request must still be admitted")
			})

			t.Run("limit is admitted in full", func(t *testing.T) {
				rl, _ := rlFrozen(rps)
				rlDrain(t, rl, key, rps)
				assert.InDelta(t, 0.0, rlTokens(rl, key), 1e-9, "bucket must be empty at the limit")
			})

			t.Run("limit+1 is rejected", func(t *testing.T) {
				rl, _ := rlFrozen(rps)
				rlDrain(t, rl, key, rps)
				assert.False(t, rl.Allow(key), "request rps+1 must be rejected")
				assert.False(t, rl.Allow(key), "rejection is stable while the clock is frozen")
			})
		})
	}
}

func TestRateLimiter_RefillBoundary(t *testing.T) {
	// rps=4 makes one token worth exactly 250ms, which is representable
	// exactly in float64 seconds, so the boundary is not a rounding artifact.
	const (
		key       = "203.0.113.9"
		rps       = 4
		oneToken  = 250 * time.Millisecond
		fullCycle = time.Second
	)

	tests := []struct {
		name    string
		advance time.Duration
		allowed bool
	}{
		{name: "no time has passed", advance: 0, allowed: false},
		{name: "one nanosecond before a token is due", advance: oneToken - time.Nanosecond, allowed: false},
		{name: "exactly one token is due", advance: oneToken, allowed: true},
		{name: "one nanosecond after a token is due", advance: oneToken + time.Nanosecond, allowed: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// A fresh limiter per case: a rejected call also advances the
			// bucket's refill mark, which would smear the boundary.
			rl, clk := rlFrozen(rps)
			rlDrain(t, rl, key, rps)
			clk.Advance(tt.advance)
			assert.Equal(t, tt.allowed, rl.Allow(key))
		})
	}

	t.Run("a full window restores the whole bucket", func(t *testing.T) {
		rl, clk := rlFrozen(rps)
		rlDrain(t, rl, key, rps)
		require.False(t, rl.Allow(key))

		clk.Advance(fullCycle)
		rlDrain(t, rl, key, rps)
		assert.False(t, rl.Allow(key), "the refilled bucket holds at most rps tokens")
	})
}

func TestRateLimiter_BurstIsCappedAtMaxTokens(t *testing.T) {
	const key = "192.0.2.44"
	rl, clk := rlFrozen(3)

	// Create the bucket, then idle: an hour of accrual must not build up an
	// unbounded burst allowance, only a full bucket.
	rlDrain(t, rl, key, 3)
	require.False(t, rl.Allow(key))
	clk.Advance(time.Hour)
	rlDrain(t, rl, key, 3)
	assert.False(t, rl.Allow(key), "burst is capped at rps regardless of idle time")
	assert.InDelta(t, 0.0, rlTokens(rl, key), 1e-9)
}

func TestRateLimiter_InvalidRPSConfiguration(t *testing.T) {
	tests := []struct {
		name string
		rps  int
	}{
		{name: "zero", rps: 0},
		{name: "negative", rps: -5},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rl, clk := rlFrozen(tt.rps)

			// A non-positive limit is a closed gate, not an open one.
			assert.False(t, rl.Allow("10.1.1.1"), "no request may be admitted with rps=%d", tt.rps)
			clk.Advance(time.Hour)
			assert.False(t, rl.Allow("10.1.1.1"), "time does not open a non-positive limit")

			handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				t.Error("backend must not be reached with a non-positive limit")
			}))
			req := httptest.NewRequest(http.MethodGet, "/test", nil)
			req.RemoteAddr = "10.1.1.1:1111"
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)
			assert.Equal(t, http.StatusTooManyRequests, rec.Code)
		})
	}
}

func TestRateLimiter_PerKeyIsolation(t *testing.T) {
	rl, clk := rlFrozen(2)

	// Exhaust one key.
	rlDrain(t, rl, "10.0.0.1", 2)
	require.False(t, rl.Allow("10.0.0.1"))

	// Neighbouring keys are untouched, each with its own full bucket.
	for _, other := range []string{"10.0.0.2", "10.0.0.3", "2001:db8::1", ""} {
		assert.True(t, rl.Allow(other), "key %q must have its own bucket", other)
		assert.True(t, rl.Allow(other), "key %q must have its own bucket", other)
		assert.False(t, rl.Allow(other), "key %q must be limited independently", other)
	}

	// Refilling is per key as well.
	clk.Advance(500 * time.Millisecond)
	assert.True(t, rl.Allow("10.0.0.1"))
	assert.Len(t, rl.buckets, 5, "one bucket per distinct key")
}

func TestRateLimiter_HandlerRejectionShape(t *testing.T) {
	const rps = 2
	rl, _ := rlFrozen(rps)

	var backendCalls int32
	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&backendCalls, 1)
		w.WriteHeader(http.StatusOK)
	}))

	do := func(remoteAddr string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, "/test", nil)
		req.RemoteAddr = remoteAddr
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		return rec
	}

	for i := 0; i < rps; i++ {
		require.Equal(t, http.StatusOK, do("192.168.5.5:9999").Code)
	}

	rec := do("192.168.5.5:9999")
	assert.Equal(t, http.StatusTooManyRequests, rec.Code)
	assert.Equal(t, "1", rec.Header().Get("Retry-After"))
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))

	var body map[string]string
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	assert.Equal(t, "rate limit exceeded", body["error"])

	// The rejected request must never reach the backend.
	assert.Equal(t, int32(rps), atomic.LoadInt32(&backendCalls))

	// A different client is not affected by the first client's rejection.
	assert.Equal(t, http.StatusOK, do("192.168.5.6:9999").Code)
	assert.Equal(t, int32(rps+1), atomic.LoadInt32(&backendCalls))
}

func TestRateLimiter_HandlerKeysByHostNotPort(t *testing.T) {
	rl, _ := rlFrozen(1)

	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	do := func(remoteAddr string) int {
		req := httptest.NewRequest(http.MethodGet, "/test", nil)
		req.RemoteAddr = remoteAddr
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		return rec.Code
	}

	// Same host, different source ports: one shared bucket.
	assert.Equal(t, http.StatusOK, do("198.51.100.20:1000"))
	assert.Equal(t, http.StatusTooManyRequests, do("198.51.100.20:2000"))

	// IPv6 literals are keyed on the address, not the bracketed host:port form.
	assert.Equal(t, http.StatusOK, do("[2001:db8::5]:1000"))
	assert.Equal(t, http.StatusTooManyRequests, do("[2001:db8::5]:2000"))

	// A RemoteAddr with no port is used verbatim rather than being dropped.
	assert.Equal(t, http.StatusOK, do("198.51.100.30"))
	assert.Equal(t, http.StatusTooManyRequests, do("198.51.100.30"))
}

func TestRateLimiter_ConcurrentAdmitsExactlyRPS(t *testing.T) {
	const (
		key       = "172.16.0.1"
		rps       = 50
		attempts  = 500
		expectErr = "the limiter must admit exactly rps requests within one frozen window"
	)

	rl, _ := rlFrozen(rps)

	var admitted int64
	var wg sync.WaitGroup
	start := make(chan struct{})
	wg.Add(attempts)
	for i := 0; i < attempts; i++ {
		go func() {
			defer wg.Done()
			<-start
			if rl.Allow(key) {
				atomic.AddInt64(&admitted, 1)
			}
		}()
	}
	close(start)
	wg.Wait()

	assert.Equal(t, int64(rps), atomic.LoadInt64(&admitted), expectErr)
	assert.InDelta(t, 0.0, rlTokens(rl, key), 1e-9)
}

func TestRateLimiter_ConcurrentKeysDoNotShareBuckets(t *testing.T) {
	const (
		rps  = 4
		keys = 25
	)

	rl, _ := rlFrozen(rps)

	admitted := make([]int64, keys)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for k := 0; k < keys; k++ {
		for i := 0; i < rps*3; i++ {
			wg.Add(1)
			go func(k int) {
				defer wg.Done()
				<-start
				if rl.Allow(fmt.Sprintf("10.9.0.%d", k)) {
					atomic.AddInt64(&admitted[k], 1)
				}
			}(k)
		}
	}
	close(start)
	wg.Wait()

	for k := 0; k < keys; k++ {
		assert.Equal(t, int64(rps), atomic.LoadInt64(&admitted[k]),
			"key %d must get its own full bucket", k)
	}
	assert.Len(t, rl.buckets, keys)
}

func TestRateLimiter_ConcurrentHandlerAdmitsExactlyRPS(t *testing.T) {
	const (
		rps      = 20
		attempts = 200
	)

	rl, _ := rlFrozen(rps)

	var served, rejected int64
	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&served, 1)
		w.WriteHeader(http.StatusOK)
	}))

	var wg sync.WaitGroup
	start := make(chan struct{})
	wg.Add(attempts)
	for i := 0; i < attempts; i++ {
		go func() {
			defer wg.Done()
			req := httptest.NewRequest(http.MethodGet, "/test", nil)
			req.RemoteAddr = "10.20.30.40:5555"
			rec := httptest.NewRecorder()
			<-start
			handler.ServeHTTP(rec, req)
			if rec.Code == http.StatusTooManyRequests {
				atomic.AddInt64(&rejected, 1)
			}
		}()
	}
	close(start)
	wg.Wait()

	assert.Equal(t, int64(rps), atomic.LoadInt64(&served))
	assert.Equal(t, int64(attempts-rps), atomic.LoadInt64(&rejected))
}

// FINDING (documented, not fixed here): stale-bucket eviction is unreachable
// from a test. RateLimiter.cleanup() is started as an unexported goroutine by
// NewRateLimiter with a hard-coded 5-minute time.Ticker and no stop channel, so
// (a) its loop body can only run after five minutes of real time and (b) every
// RateLimiter ever constructed leaks a goroutine for the life of the process.
// Making it testable requires a production seam (injectable ticker + Close()),
// which is out of scope for a coverage-only change.
func TestRateLimiter_EvictsStaleBuckets(t *testing.T) {
	t.Skip("no seam to drive cleanup(): hard-coded 5m ticker, no stop channel — see FINDING above")

	rl, clk := rlFrozen(1)
	require.True(t, rl.Allow("10.0.0.1"))
	clk.Advance(11 * time.Minute)

	assert.Empty(t, rl.buckets, "buckets idle for more than 10 minutes should be evicted")
}
