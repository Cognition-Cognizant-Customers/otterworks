package middleware

import (
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fixedClock drives the limiter from the test rather than the wall clock, so
// every refill boundary is exact and no test ever sleeps.
type fixedClock struct {
	mu sync.Mutex
	t  time.Time
}

func newFixedClock() *fixedClock {
	return &fixedClock{t: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)}
}

func (c *fixedClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fixedClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

// newClockedLimiter builds a limiter whose clock the test owns. The background
// cleanup goroutine started by NewRateLimiter only fires on a 5-minute ticker, so
// it cannot interfere with a test.
func newClockedLimiter(rps int) (*RateLimiter, *fixedClock) {
	clock := newFixedClock()
	rl := NewRateLimiter(rps)
	rl.now = clock.Now
	return rl, clock
}

func TestRateLimiter_RequestsUpToTheLimit_AreAllowed(t *testing.T) {
	rl, _ := newClockedLimiter(5)

	for i := 1; i <= 5; i++ {
		assert.True(t, rl.Allow("10.0.0.1"), "request %d of 5 must be allowed", i)
	}
}

func TestRateLimiter_RequestAtLimitPlusOne_IsDenied(t *testing.T) {
	rl, _ := newClockedLimiter(5)

	for i := 0; i < 5; i++ {
		require.True(t, rl.Allow("10.0.0.1"))
	}

	assert.False(t, rl.Allow("10.0.0.1"), "the 6th request in the same instant must be denied")
}

func TestRateLimiter_PartialSecondElapsed_RefillsFractionally(t *testing.T) {
	rl, clock := newClockedLimiter(10)

	for i := 0; i < 10; i++ {
		require.True(t, rl.Allow("10.0.0.1"))
	}
	require.False(t, rl.Allow("10.0.0.1"))

	// 99 ms at 10 tokens/s is 0.99 tokens: still short of one whole token.
	clock.Advance(99 * time.Millisecond)
	assert.False(t, rl.Allow("10.0.0.1"), "0.99 tokens is not a whole token")

	// The denied call still consumed the elapsed time, so one more millisecond
	// short of the boundary is enough to cross it.
	clock.Advance(101 * time.Millisecond)
	assert.True(t, rl.Allow("10.0.0.1"))
}

func TestRateLimiter_RefillIsCappedAtTheConfiguredBurst(t *testing.T) {
	rl, clock := newClockedLimiter(3)

	for i := 0; i < 3; i++ {
		require.True(t, rl.Allow("10.0.0.1"))
	}

	// An hour of idling must not bank an hour's worth of tokens.
	clock.Advance(time.Hour)

	for i := 1; i <= 3; i++ {
		assert.True(t, rl.Allow("10.0.0.1"), "burst request %d must be allowed", i)
	}
	assert.False(t, rl.Allow("10.0.0.1"), "the bucket must not exceed maxTokens after a long idle")
}

func TestRateLimiter_ClockMovingBackwards_DoesNotGrantExtraTokens(t *testing.T) {
	rl, clock := newClockedLimiter(2)

	require.True(t, rl.Allow("10.0.0.1"))
	require.True(t, rl.Allow("10.0.0.1"))

	// NTP correction / container clock skew: elapsed goes negative, so the refill
	// term is negative and must not be mistaken for available budget.
	clock.Advance(-time.Hour)

	assert.False(t, rl.Allow("10.0.0.1"), "a backwards clock must not unlock the bucket")
}

func TestRateLimiter_ZeroRPS_DeniesEveryRequest(t *testing.T) {
	rl, clock := newClockedLimiter(0)

	assert.False(t, rl.Allow("10.0.0.1"), "a limit of 0 means no request is ever allowed")

	clock.Advance(time.Hour)
	assert.False(t, rl.Allow("10.0.0.1"), "a 0 refill rate never accrues tokens")
}

func TestRateLimiter_NegativeRPS_DeniesEveryRequest(t *testing.T) {
	rl, clock := newClockedLimiter(-1)

	assert.False(t, rl.Allow("10.0.0.1"))

	clock.Advance(time.Hour)
	assert.False(t, rl.Allow("10.0.0.1"), "a negative refill rate must not wrap into an allowance")
}

func TestRateLimiter_SeparateIPs_HaveIndependentBudgets(t *testing.T) {
	rl, _ := newClockedLimiter(1)

	require.True(t, rl.Allow("10.0.0.1"))
	require.False(t, rl.Allow("10.0.0.1"))

	assert.True(t, rl.Allow("10.0.0.2"), "exhausting one IP must not throttle another")
}

func TestRateLimiter_ConcurrentCallers_AllowExactlyTheLimit(t *testing.T) {
	const limit = 50
	rl, _ := newClockedLimiter(limit)

	// The clock is frozen, so no refill can occur mid-run and the total number of
	// allowed requests is deterministic no matter how the goroutines interleave.
	var (
		wg      sync.WaitGroup
		mu      sync.Mutex
		allowed int
	)
	for i := 0; i < limit*4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if rl.Allow("10.0.0.1") {
				mu.Lock()
				allowed++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()

	assert.Equal(t, limit, allowed, "concurrent callers must not be able to overdraw the bucket")
}

func TestRateLimiter_HandlerOnDeniedRequest_DoesNotInvokeTheNextHandler(t *testing.T) {
	rl, _ := newClockedLimiter(1)

	var reached int
	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached++
		w.WriteHeader(http.StatusOK)
	}))

	for i := 0; i < 3; i++ {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/files", nil)
		req.RemoteAddr = "203.0.113.7:44321"
		handler.ServeHTTP(httptest.NewRecorder(), req)
	}

	assert.Equal(t, 1, reached, "a throttled request must be short-circuited, not forwarded")
}

func TestRateLimiter_HandlerOnDeniedRequest_SetsRetryAfterAndJSONBody(t *testing.T) {
	rl, _ := newClockedLimiter(1)
	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	var rec *httptest.ResponseRecorder
	for i := 0; i < 2; i++ {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/files", nil)
		req.RemoteAddr = "203.0.113.8:44321"
		rec = httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
	}

	assert.Equal(t, http.StatusTooManyRequests, rec.Code)
	assert.Equal(t, "1", rec.Header().Get("Retry-After"))
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))
	assert.JSONEq(t, `{"error":"rate limit exceeded"}`, rec.Body.String())
}

func TestRateLimiter_HandlerBucketsByIPNotByPort(t *testing.T) {
	rl, _ := newClockedLimiter(1)
	handler := rl.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	first := httptest.NewRequest(http.MethodGet, "/api/v1/files", nil)
	first.RemoteAddr = "203.0.113.9:1111"
	firstRec := httptest.NewRecorder()
	handler.ServeHTTP(firstRec, first)
	require.Equal(t, http.StatusOK, firstRec.Code)

	// Same client, new source port: a per-port bucket would hand out a fresh
	// allowance and make the limit trivially bypassable.
	second := httptest.NewRequest(http.MethodGet, "/api/v1/files", nil)
	second.RemoteAddr = "203.0.113.9:2222"
	secondRec := httptest.NewRecorder()
	handler.ServeHTTP(secondRec, second)

	assert.Equal(t, http.StatusTooManyRequests, secondRec.Code)
}

func TestExtractIP_IPv6RemoteAddr_ReturnsBareAddress(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = "[2001:db8::1]:8080"

	assert.Equal(t, "2001:db8::1", extractIP(req))
}

func TestExtractIP_RemoteAddrWithoutPort_ReturnsItUnchanged(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = "203.0.113.10"

	assert.Equal(t, "203.0.113.10", extractIP(req))
}

func TestExtractIP_EmptyRemoteAddr_ReturnsEmptyString(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = ""

	// All callers with an unparseable RemoteAddr collapse into one shared bucket.
	assert.Equal(t, "", extractIP(req))
}
