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
// Boundary, negative and concurrency coverage for the token bucket.
//
// Every test below drives time through the limiter's injectable clock
// (RateLimiter.now); nothing sleeps or reads the wall clock, so results do not
// depend on scheduling or on the order tests run in.
// ---------------------------------------------------------------------------

// rlFixedClock pins a limiter's clock to base and returns a function that
// advances it. The clock is swapped under the limiter's mutex because the
// background cleanup goroutine reads the same field.
func rlFixedClock(rl *RateLimiter, base time.Time) func(time.Duration) {
	set := func(t time.Time) {
		rl.mu.Lock()
		defer rl.mu.Unlock()
		rl.now = func() time.Time { return t }
	}
	set(base)
	return func(d time.Duration) { set(base.Add(d)) }
}

// rlEpoch is a fixed instant; using a constant rather than time.Now keeps the
// arithmetic in the tests independent of when they run.
func rlEpoch() time.Time {
	return time.Date(2026, time.January, 2, 3, 4, 5, 0, time.UTC)
}

func rlNewFixed(rps int) (*RateLimiter, func(time.Duration)) {
	rl := NewRateLimiter(rps)
	return rl, rlFixedClock(rl, rlEpoch())
}

func TestRateLimiter_BoundaryTrioAtConfiguredRPS(t *testing.T) {
	for _, rps := range []int{1, 2, 5, 100} {
		t.Run(fmt.Sprintf("rps=%d", rps), func(t *testing.T) {
			rl, _ := rlNewFixed(rps)
			const key = "198.51.100.7"

			// rps-1: strictly below the limit, all allowed.
			for i := 1; i < rps; i++ {
				require.True(t, rl.Allow(key), "request %d of %d should be allowed", i, rps)
			}
			// rps: exactly at the limit, still allowed.
			assert.True(t, rl.Allow(key), "request %d (== rps) should be allowed", rps)
			// rps+1: over the limit, denied.
			assert.False(t, rl.Allow(key), "request %d (== rps+1) should be denied", rps+1)
			// Further requests stay denied while the clock is frozen.
			assert.False(t, rl.Allow(key), "request %d should stay denied", rps+2)
		})
	}
}

func TestRateLimiter_NonPositiveRPSDeniesEverything(t *testing.T) {
	for _, rps := range []int{0, -1} {
		t.Run(fmt.Sprintf("rps=%d", rps), func(t *testing.T) {
			rl, advance := rlNewFixed(rps)
			assert.False(t, rl.Allow("203.0.113.9"), "a bucket with no capacity must deny the first request")
			advance(time.Hour)
			assert.False(t, rl.Allow("203.0.113.9"), "waiting cannot create capacity when the refill rate is <= 0")
		})
	}
}

// The bucket refills at rps tokens/second, so one token takes 1/rps seconds.
// rps=4 makes that window exactly 250ms, which is representable in float64 and
// therefore an exact boundary rather than an approximate one.
func TestRateLimiter_RefillBoundaryTrioForOneToken(t *testing.T) {
	const rps = 4
	const window = time.Second / rps // 250ms == one token

	tests := []struct {
		name    string
		elapsed time.Duration
		allowed bool
	}{
		{"just under one token", window - time.Millisecond, false},
		{"exactly one token", window, true},
		{"just over one token", window + time.Millisecond, true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rl, advance := rlNewFixed(rps)
			const key = "192.0.2.10"
			for i := 0; i < rps; i++ {
				require.True(t, rl.Allow(key))
			}
			require.False(t, rl.Allow(key), "bucket should be empty")

			advance(tt.elapsed)
			assert.Equal(t, tt.allowed, rl.Allow(key))
			if tt.allowed {
				assert.False(t, rl.Allow(key), "only one token should have been refilled")
			}
		})
	}
}

func TestRateLimiter_FullWindowRefillRestoresExactlyRPSTokens(t *testing.T) {
	const rps = 3
	rl, advance := rlNewFixed(rps)
	const key = "192.0.2.11"

	for i := 0; i < rps; i++ {
		require.True(t, rl.Allow(key))
	}
	require.False(t, rl.Allow(key))

	advance(time.Second)
	for i := 0; i < rps; i++ {
		assert.True(t, rl.Allow(key), "token %d should be available after a full window", i+1)
	}
	assert.False(t, rl.Allow(key), "the window must not refill more than rps tokens")
}

func TestRateLimiter_IdleDoesNotAccumulateBeyondBurstSize(t *testing.T) {
	const rps = 2
	rl, advance := rlNewFixed(rps)
	const key = "192.0.2.12"

	require.True(t, rl.Allow(key))
	require.True(t, rl.Allow(key))
	require.False(t, rl.Allow(key))

	// An hour idle is 7200 tokens' worth of time; the bucket caps at rps.
	advance(time.Hour)
	for i := 0; i < rps; i++ {
		assert.True(t, rl.Allow(key), "burst token %d should be available", i+1)
	}
	assert.False(t, rl.Allow(key), "accumulated tokens must be capped at maxTokens")
}

func TestRateLimiter_PerKeyIsolation(t *testing.T) {
	const rps = 3
	rl, advance := rlNewFixed(rps)
	const noisy = "192.0.2.20"
	const quiet = "192.0.2.21"

	// Exhaust one client's budget entirely.
	for i := 0; i < rps; i++ {
		require.True(t, rl.Allow(noisy))
	}
	require.False(t, rl.Allow(noisy))

	// The other client still has its full budget.
	for i := 0; i < rps; i++ {
		assert.True(t, rl.Allow(quiet), "quiet client token %d must not have been consumed by the noisy one", i+1)
	}
	assert.False(t, rl.Allow(quiet))
	assert.False(t, rl.Allow(noisy), "the noisy client must stay limited")

	// Refill is per key as well.
	advance(time.Second)
	assert.True(t, rl.Allow(noisy))
	assert.True(t, rl.Allow(quiet))
}

func TestRateLimiter_HandlerBoundaryTrio(t *testing.T) {
	const rps = 3
	rl, _ := rlNewFixed(rps)

	var served int64
	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&served, 1)
		w.WriteHeader(http.StatusOK)
	}))

	do := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, "/test", nil)
		req.RemoteAddr = "192.0.2.30:44444"
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		return rec
	}

	for i := 1; i < rps; i++ {
		require.Equal(t, http.StatusOK, do().Code, "request %d (below rps) should pass", i)
	}
	assert.Equal(t, http.StatusOK, do().Code, "request %d (== rps) should pass", rps)

	rec := do()
	assert.Equal(t, http.StatusTooManyRequests, rec.Code, "request %d (== rps+1) should be limited", rps+1)
	assert.Equal(t, int64(rps), atomic.LoadInt64(&served), "the limited request must not reach the next handler")
}

func TestRateLimiter_HandlerLimitedResponseShape(t *testing.T) {
	rl, _ := rlNewFixed(1)
	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	req.RemoteAddr = "192.0.2.31:1111"
	require.Equal(t, http.StatusOK, func() int {
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		return rec.Code
	}())

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	require.Equal(t, http.StatusTooManyRequests, rec.Code)
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))
	assert.Equal(t, "1", rec.Header().Get("Retry-After"))

	var body map[string]string
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	assert.Equal(t, "rate limit exceeded", body["error"])
}

func TestRateLimiter_HandlerBucketsByIPNotByPort(t *testing.T) {
	rl, _ := rlNewFixed(2)
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

	// Same client, different ephemeral ports: one shared budget.
	assert.Equal(t, http.StatusOK, do("192.0.2.40:1000"))
	assert.Equal(t, http.StatusOK, do("192.0.2.40:2000"))
	assert.Equal(t, http.StatusTooManyRequests, do("192.0.2.40:3000"))

	// A different client is unaffected.
	assert.Equal(t, http.StatusOK, do("192.0.2.41:1000"))
}

func TestExtractIP_EdgeCases(t *testing.T) {
	tests := []struct {
		name       string
		remoteAddr string
		expected   string
	}{
		{"IPv6 with port", "[2001:db8::1]:8080", "2001:db8::1"},
		{"IPv6 loopback with port", "[::1]:443", "::1"},
		{"IPv6 without brackets or port", "2001:db8::1", "2001:db8::1"},
		{"empty RemoteAddr", "", ""},
		{"trailing colon without port", "10.0.0.1:", "10.0.0.1"},
		{"unix socket style address", "@", "@"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/", nil)
			req.RemoteAddr = tt.remoteAddr
			assert.Equal(t, tt.expected, extractIP(req))
		})
	}
}

func TestRateLimiter_ConcurrentRequestsForOneKeyAllowExactlyRPS(t *testing.T) {
	const rps = 8
	const callers = rps * 4
	rl, _ := rlNewFixed(rps)

	var allowed int64
	var start sync.WaitGroup
	var done sync.WaitGroup
	start.Add(1)
	done.Add(callers)

	for i := 0; i < callers; i++ {
		go func() {
			defer done.Done()
			start.Wait()
			if rl.Allow("192.0.2.50") {
				atomic.AddInt64(&allowed, 1)
			}
		}()
	}
	start.Done()
	done.Wait()

	// The clock is frozen, so no tokens are refilled mid-flight: the bucket can
	// hand out exactly rps tokens no matter how the goroutines interleave.
	assert.Equal(t, int64(rps), atomic.LoadInt64(&allowed))
}

func TestRateLimiter_ConcurrentRequestsForDistinctKeysDoNotShareBudget(t *testing.T) {
	const rps = 4
	const keys = 6
	rl, _ := rlNewFixed(rps)

	allowed := make([]int64, keys)
	var start sync.WaitGroup
	var done sync.WaitGroup
	start.Add(1)
	done.Add(keys * rps * 2)

	for k := 0; k < keys; k++ {
		for i := 0; i < rps*2; i++ {
			go func(k int) {
				defer done.Done()
				start.Wait()
				if rl.Allow(fmt.Sprintf("192.0.2.%d", 100+k)) {
					atomic.AddInt64(&allowed[k], 1)
				}
			}(k)
		}
	}
	start.Done()
	done.Wait()

	for k := 0; k < keys; k++ {
		assert.Equal(t, int64(rps), atomic.LoadInt64(&allowed[k]), "key %d should get its own full budget", k)
	}
}
