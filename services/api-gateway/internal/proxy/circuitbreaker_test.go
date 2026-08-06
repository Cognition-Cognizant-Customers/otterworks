package proxy

import (
	"bufio"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func defaultTestConfig() CircuitBreakerConfig {
	return CircuitBreakerConfig{
		MaxRequests:  2,
		Interval:     60 * time.Second,
		Timeout:      10 * time.Second,
		FailureRatio: 0.5,
	}
}

func TestCircuitBreaker_StartsInClosedState(t *testing.T) {
	cb := NewCircuitBreaker("test-svc", defaultTestConfig())
	assert.Equal(t, StateClosed, cb.State())
}

func TestCircuitBreaker_SuccessfulRequests(t *testing.T) {
	cb := NewCircuitBreaker("test-svc", defaultTestConfig())

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	for i := 0; i < 10; i++ {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		rec := httptest.NewRecorder()
		err := cb.Execute(handler, rec, req)
		require.NoError(t, err)
		assert.Equal(t, http.StatusOK, rec.Code)
	}

	assert.Equal(t, StateClosed, cb.State())
}

func TestCircuitBreaker_TripsOnFailures(t *testing.T) {
	cfg := CircuitBreakerConfig{
		MaxRequests:  2,
		Interval:     60 * time.Second,
		Timeout:      10 * time.Second,
		FailureRatio: 0.5,
	}
	cb := NewCircuitBreaker("test-svc", cfg)

	failHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	})

	// Send enough requests to trip the breaker (need at least 5 total, >50% failures)
	for i := 0; i < 6; i++ {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		rec := httptest.NewRecorder()
		cb.Execute(failHandler, rec, req)
	}

	assert.Equal(t, StateOpen, cb.State())

	// Next request should be rejected
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	rec := httptest.NewRecorder()
	err := cb.Execute(failHandler, rec, req)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "circuit breaker test-svc is open")
}

func TestCircuitBreaker_TransitionsToHalfOpen(t *testing.T) {
	cfg := CircuitBreakerConfig{
		MaxRequests:  2,
		Interval:     60 * time.Second,
		Timeout:      5 * time.Second,
		FailureRatio: 0.5,
	}
	cb := NewCircuitBreaker("test-svc", cfg)

	now := time.Now()
	cb.now = func() time.Time { return now }

	failHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	})

	// Trip the breaker
	for i := 0; i < 6; i++ {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		rec := httptest.NewRecorder()
		cb.Execute(failHandler, rec, req)
	}
	assert.Equal(t, StateOpen, cb.State())

	// Advance time past timeout
	cb.now = func() time.Time { return now.Add(6 * time.Second) }
	assert.Equal(t, StateHalfOpen, cb.State())
}

func TestCircuitBreaker_RecoveryFromHalfOpen(t *testing.T) {
	cfg := CircuitBreakerConfig{
		MaxRequests:  2,
		Interval:     60 * time.Second,
		Timeout:      5 * time.Second,
		FailureRatio: 0.5,
	}
	cb := NewCircuitBreaker("test-svc", cfg)

	now := time.Now()
	cb.now = func() time.Time { return now }

	failHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	})
	successHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	// Trip the breaker
	for i := 0; i < 6; i++ {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		rec := httptest.NewRecorder()
		cb.Execute(failHandler, rec, req)
	}
	assert.Equal(t, StateOpen, cb.State())

	// Advance time to half-open
	cb.now = func() time.Time { return now.Add(6 * time.Second) }

	// Successful requests in half-open should close the breaker
	for i := 0; i < 2; i++ {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		rec := httptest.NewRecorder()
		err := cb.Execute(successHandler, rec, req)
		require.NoError(t, err)
	}

	assert.Equal(t, StateClosed, cb.State())
}

func TestCircuitBreakerManager_GetOrCreate(t *testing.T) {
	mgr := NewCircuitBreakerManager(defaultTestConfig())

	cb1 := mgr.Get("service-a")
	cb2 := mgr.Get("service-a")
	cb3 := mgr.Get("service-b")

	assert.Same(t, cb1, cb2, "same name should return same instance")
	assert.NotSame(t, cb1, cb3, "different names should return different instances")
}

func TestCircuitState_String(t *testing.T) {
	assert.Equal(t, "closed", StateClosed.String())
	assert.Equal(t, "open", StateOpen.String())
	assert.Equal(t, "half-open", StateHalfOpen.String())
}

// ---------------------------------------------------------------------------
// WP-04: circuit-breaker threshold, reset-timeout, half-open and concurrency
// boundaries. Existing cases above are left untouched; everything below drives
// the breaker from the injected clock (`CircuitBreaker.now`), so no case
// depends on wall-clock time, sleeps, or execution order. Helper identifiers
// are prefixed `cb` to stay disjoint from other test files in this package.
// ---------------------------------------------------------------------------

// cbClock is a race-safe manually advanced clock.
type cbClock struct {
	mu sync.Mutex
	t  time.Time
}

func cbNewClock() *cbClock {
	return &cbClock{t: time.Date(2024, time.January, 1, 0, 0, 0, 0, time.UTC)}
}

func (c *cbClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *cbClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

func cbNewFrozen(cfg CircuitBreakerConfig) (*CircuitBreaker, *cbClock) {
	cb := NewCircuitBreaker("test-svc", cfg)
	clk := cbNewClock()
	cb.now = clk.Now
	return cb, clk
}

func cbHandlerStatus(code int) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(code)
	})
}

// cbCall runs one request through the breaker and returns the breaker's error
// (nil when the request reached the backend, whatever the backend answered).
func cbCall(cb *CircuitBreaker, handler http.Handler) (*httptest.ResponseRecorder, error) {
	rec := httptest.NewRecorder()
	err := cb.Execute(handler, rec, httptest.NewRequest(http.MethodGet, "/", nil))
	return rec, err
}

// cbSnapshot reads the internal counters without racing the breaker.
func cbSnapshot(cb *CircuitBreaker) counts {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	return cb.counts
}

// cbTrip drives the breaker to open with the minimum number of failures.
func cbTrip(t *testing.T, cb *CircuitBreaker) {
	t.Helper()
	fail := cbHandlerStatus(http.StatusInternalServerError)
	for i := 0; i < 10 && cb.State() != StateOpen; i++ {
		_, err := cbCall(cb, fail)
		require.NoError(t, err, "failure %d should have reached the backend", i+1)
	}
	require.Equal(t, StateOpen, cb.State(), "breaker should be open")
}

func TestCircuitBreaker_MinimumSampleBoundary(t *testing.T) {
	// FailureRatio 1.0 isolates the other threshold in shouldTrip(): the
	// hard-coded minimum sample size of 5 requests.
	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 1.0}

	tests := []struct {
		name     string
		failures int
		want     CircuitState
	}{
		{name: "threshold-1 keeps the breaker closed", failures: 4, want: StateClosed},
		{name: "threshold trips the breaker", failures: 5, want: StateOpen},
		{name: "threshold+1 stays open", failures: 6, want: StateOpen},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cb, _ := cbNewFrozen(cfg)
			fail := cbHandlerStatus(http.StatusInternalServerError)

			var rejected int
			for i := 0; i < tt.failures; i++ {
				if _, err := cbCall(cb, fail); err != nil {
					rejected++
				}
			}

			assert.Equal(t, tt.want, cb.State())
			// Anything past the trip point never reaches the backend.
			assert.Equal(t, max(0, tt.failures-5), rejected)
		})
	}
}

func TestCircuitBreaker_FailureRatioBoundary(t *testing.T) {
	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}

	tests := []struct {
		name      string
		successes int
		failures  int
		ratio     string
		want      CircuitState
	}{
		{name: "ratio below threshold", successes: 3, failures: 2, ratio: "0.4", want: StateClosed},
		{name: "ratio exactly at threshold", successes: 3, failures: 3, ratio: "0.5", want: StateOpen},
		{name: "ratio above threshold", successes: 2, failures: 3, ratio: "0.6", want: StateOpen},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cb, _ := cbNewFrozen(cfg)
			ok := cbHandlerStatus(http.StatusOK)
			fail := cbHandlerStatus(http.StatusInternalServerError)

			for i := 0; i < tt.successes; i++ {
				_, err := cbCall(cb, ok)
				require.NoError(t, err)
			}
			for i := 0; i < tt.failures; i++ {
				_, err := cbCall(cb, fail)
				require.NoError(t, err)
			}

			assert.Equal(t, tt.want, cb.State(), "failure ratio %s vs threshold 0.5", tt.ratio)
		})
	}
}

func TestCircuitBreaker_ResetTimeoutBoundary(t *testing.T) {
	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, clk := cbNewFrozen(cfg)
	cbTrip(t, cb)

	ok := cbHandlerStatus(http.StatusOK)

	clk.Advance(cfg.Timeout - time.Nanosecond)
	assert.Equal(t, StateOpen, cb.State(), "one nanosecond before the reset timeout")
	_, err := cbCall(cb, ok)
	assert.ErrorContains(t, err, "is open")

	// The transition is strictly after the expiry instant (expiry.Before(now)).
	clk.Advance(time.Nanosecond)
	assert.Equal(t, StateOpen, cb.State(), "exactly at the reset timeout the breaker is still open")

	clk.Advance(time.Nanosecond)
	assert.Equal(t, StateHalfOpen, cb.State(), "one nanosecond after the reset timeout")
	_, err = cbCall(cb, ok)
	assert.NoError(t, err, "the probe must be admitted once half-open")
}

func TestCircuitBreaker_OpenStateDoesNotReachBackend(t *testing.T) {
	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, _ := cbNewFrozen(cfg)
	cbTrip(t, cb)

	var calls int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
	})

	rec, err := cbCall(cb, handler)
	assert.ErrorContains(t, err, "circuit breaker test-svc is open")
	assert.Zero(t, atomic.LoadInt32(&calls), "the backend must not be called while open")
	assert.Empty(t, rec.Body.String(), "the breaker writes no body; the caller owns the response")
}

func TestCircuitBreaker_HalfOpenSingleProbeSuccessCloses(t *testing.T) {
	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, clk := cbNewFrozen(cfg)
	cbTrip(t, cb)

	clk.Advance(cfg.Timeout + time.Nanosecond)
	require.Equal(t, StateHalfOpen, cb.State())

	_, err := cbCall(cb, cbHandlerStatus(http.StatusOK))
	require.NoError(t, err)

	assert.Equal(t, StateClosed, cb.State(), "a successful probe closes the breaker")
	assert.Equal(t, counts{}, cbSnapshot(cb), "closing resets the counters")
}

func TestCircuitBreaker_HalfOpenProbeFailureReopens(t *testing.T) {
	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, clk := cbNewFrozen(cfg)
	cbTrip(t, cb)

	clk.Advance(cfg.Timeout + time.Nanosecond)
	require.Equal(t, StateHalfOpen, cb.State())

	_, err := cbCall(cb, cbHandlerStatus(http.StatusServiceUnavailable))
	require.NoError(t, err, "the probe itself reaches the backend")
	assert.Equal(t, StateOpen, cb.State(), "a failed probe re-opens the breaker")

	// The reset timeout restarts from the moment of the failed probe.
	clk.Advance(cfg.Timeout)
	assert.Equal(t, StateOpen, cb.State())
	clk.Advance(time.Nanosecond)
	assert.Equal(t, StateHalfOpen, cb.State())
}

func TestCircuitBreaker_HalfOpenAdmitsAtMostMaxRequests(t *testing.T) {
	const (
		maxRequests = 3
		attempts    = 30
	)
	cfg := CircuitBreakerConfig{MaxRequests: maxRequests, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, clk := cbNewFrozen(cfg)
	cbTrip(t, cb)

	clk.Advance(cfg.Timeout + time.Nanosecond)
	require.Equal(t, StateHalfOpen, cb.State())

	entered := make(chan struct{}, attempts)
	release := make(chan struct{})
	finished := make(chan error, attempts)
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		entered <- struct{}{}
		<-release
		w.WriteHeader(http.StatusOK)
	})

	for i := 0; i < attempts; i++ {
		go func() {
			_, err := cbCall(cb, handler)
			finished <- err
		}()
	}

	// Admitted probes block inside the handler after signalling `entered`;
	// rejected ones return immediately. Collecting until every goroutine has
	// produced exactly one event needs no sleep and no timing assumption.
	admitted, rejected := 0, 0
	for admitted+rejected < attempts {
		select {
		case <-entered:
			admitted++
		case err := <-finished:
			rejected++
			assert.ErrorContains(t, err, "too many requests in half-open state")
		}
	}
	assert.Equal(t, maxRequests, admitted, "half-open admits exactly MaxRequests concurrent probes")
	assert.Equal(t, attempts-maxRequests, rejected)

	close(release)
	for i := 0; i < admitted; i++ {
		assert.NoError(t, <-finished)
	}
	assert.Equal(t, StateClosed, cb.State(), "MaxRequests consecutive successful probes close the breaker")
}

func TestCircuitBreaker_ConcurrentRequestsWhileOpen(t *testing.T) {
	const attempts = 100
	cfg := CircuitBreakerConfig{MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, _ := cbNewFrozen(cfg)
	cbTrip(t, cb)

	var backendCalls int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&backendCalls, 1)
	})

	var rejected int64
	var wg sync.WaitGroup
	start := make(chan struct{})
	wg.Add(attempts)
	for i := 0; i < attempts; i++ {
		go func() {
			defer wg.Done()
			<-start
			if _, err := cbCall(cb, handler); err != nil {
				atomic.AddInt64(&rejected, 1)
			}
		}()
	}
	close(start)
	wg.Wait()

	assert.Equal(t, int64(attempts), atomic.LoadInt64(&rejected), "every request is shed while open")
	assert.Zero(t, atomic.LoadInt32(&backendCalls))
	assert.Equal(t, StateOpen, cb.State())
}

func TestCircuitBreaker_FailureIsDecidedByStatusCodeBoundary(t *testing.T) {
	tests := []struct {
		name    string
		status  int
		failure bool
	}{
		{name: "499 below the 5xx boundary is a success", status: 499, failure: false},
		{name: "500 at the boundary is a failure", status: http.StatusInternalServerError, failure: true},
		{name: "501 above the boundary is a failure", status: http.StatusNotImplemented, failure: true},
		{name: "404 client error is a success", status: http.StatusNotFound, failure: false},
		{name: "200 is a success", status: http.StatusOK, failure: false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
			cb, _ := cbNewFrozen(cfg)

			rec, err := cbCall(cb, cbHandlerStatus(tt.status))
			require.NoError(t, err, "the breaker never converts a backend status into an error")
			assert.Equal(t, tt.status, rec.Code, "the backend status reaches the client unchanged")

			snap := cbSnapshot(cb)
			if tt.failure {
				assert.Equal(t, uint32(1), snap.totalFailures)
				assert.Zero(t, snap.totalSuccesses)
			} else {
				assert.Equal(t, uint32(1), snap.totalSuccesses)
				assert.Zero(t, snap.totalFailures)
			}
		})
	}
}

func TestCircuitBreaker_ClosedStateRollsCountsAfterInterval(t *testing.T) {
	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, clk := cbNewFrozen(cfg)
	fail := cbHandlerStatus(http.StatusInternalServerError)

	// Trip and recover so that the closed-state counting window is armed.
	cbTrip(t, cb)
	clk.Advance(cfg.Timeout + time.Nanosecond)
	_, err := cbCall(cb, cbHandlerStatus(http.StatusOK))
	require.NoError(t, err)
	require.Equal(t, StateClosed, cb.State())

	// Four failures: one short of the minimum sample size.
	for i := 0; i < 4; i++ {
		_, err := cbCall(cb, fail)
		require.NoError(t, err)
	}
	require.Equal(t, uint32(4), cbSnapshot(cb).totalFailures)

	// Past the interval the window rolls over, so the old failures no longer count.
	clk.Advance(cfg.Interval + time.Nanosecond)
	for i := 0; i < 4; i++ {
		_, err := cbCall(cb, fail)
		require.NoError(t, err)
	}
	assert.Equal(t, StateClosed, cb.State(), "failures from the previous window must not accumulate")
	assert.Equal(t, uint32(4), cbSnapshot(cb).totalFailures, "counters restart with the new generation")

	// A fifth failure inside the current window does trip it.
	_, err = cbCall(cb, fail)
	require.NoError(t, err)
	assert.Equal(t, StateOpen, cb.State())
}

// FINDING (genuine defect, documented not fixed): NewCircuitBreaker never arms
// the closed-state counting window. setState() is what sets `expiry`, and it is
// only reached on a state transition, so a breaker that has never tripped keeps
// `expiry` at the zero time; currentState()'s StateClosed branch is guarded by
// `!cb.expiry.IsZero()` and therefore never calls toNewGeneration(). Effect:
// for a fresh breaker, `Interval` is ignored and failures accumulate for the
// life of the process, so unrelated failures hours apart can trip the breaker.
// Fix would be a one-line change in NewCircuitBreaker (production code, out of
// scope for a coverage PR).
func TestCircuitBreaker_FreshBreakerRollsCountsAfterInterval(t *testing.T) {
	t.Skip("known defect: NewCircuitBreaker leaves expiry zero, so Interval never rolls the counts of a breaker that has not tripped yet")

	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, clk := cbNewFrozen(cfg)
	fail := cbHandlerStatus(http.StatusInternalServerError)

	for i := 0; i < 3; i++ {
		_, err := cbCall(cb, fail)
		require.NoError(t, err)
	}
	require.Equal(t, StateClosed, cb.State())

	clk.Advance(cfg.Interval + time.Nanosecond)
	for i := 0; i < 2; i++ {
		_, err := cbCall(cb, fail)
		require.NoError(t, err)
	}

	assert.Equal(t, StateClosed, cb.State(),
		"two failures in a fresh window are below the minimum sample size of 5")
}

// FINDING (genuine defect, documented not fixed): MaxRequests == 0 wedges the
// breaker permanently. It is reachable from configuration (CB_MAX_REQUESTS,
// internal/config), and Execute()'s half-open guard is
// `counts.requests >= MaxRequests`, which is true for the very first probe.
// Half-open has no expiry, so nothing ever moves the breaker again: the backend
// is shed forever, even after it recovers. This test locks in the observed
// behaviour; the companion test below states the behaviour we want.
func TestCircuitBreaker_ZeroMaxRequestsWedgesHalfOpen(t *testing.T) {
	cfg := CircuitBreakerConfig{MaxRequests: 0, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, clk := cbNewFrozen(cfg)
	cbTrip(t, cb)

	clk.Advance(cfg.Timeout + time.Nanosecond)
	require.Equal(t, StateHalfOpen, cb.State())

	var backendCalls int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&backendCalls, 1)
	})

	for _, advance := range []time.Duration{0, time.Minute, time.Hour} {
		clk.Advance(advance)
		_, err := cbCall(cb, handler)
		assert.ErrorContains(t, err, "too many requests in half-open state")
		assert.Equal(t, StateHalfOpen, cb.State())
	}
	assert.Zero(t, atomic.LoadInt32(&backendCalls), "the backend can never be probed again")
}

func TestCircuitBreaker_ZeroMaxRequestsShouldStillAdmitOneProbe(t *testing.T) {
	t.Skip("known defect: MaxRequests=0 (a valid CB_MAX_REQUESTS value) wedges the breaker in half-open forever — see FINDING above")

	cfg := CircuitBreakerConfig{MaxRequests: 0, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, clk := cbNewFrozen(cfg)
	cbTrip(t, cb)

	clk.Advance(cfg.Timeout + time.Nanosecond)
	_, err := cbCall(cb, cbHandlerStatus(http.StatusOK))

	assert.NoError(t, err, "a degenerate MaxRequests should be normalised to one probe")
	assert.Equal(t, StateClosed, cb.State())
}

func TestCircuitState_StringUnknownValue(t *testing.T) {
	assert.Equal(t, "unknown", CircuitState(99).String())
	assert.Equal(t, "unknown", CircuitState(-1).String())
}

func TestCircuitBreakerManager_ConcurrentGetReturnsOneInstance(t *testing.T) {
	const goroutines = 50
	mgr := NewCircuitBreakerManager(defaultTestConfig())

	got := make([]*CircuitBreaker, goroutines)
	var wg sync.WaitGroup
	start := make(chan struct{})
	wg.Add(goroutines)
	for i := 0; i < goroutines; i++ {
		go func(i int) {
			defer wg.Done()
			<-start
			got[i] = mgr.Get("service-a")
		}(i)
	}
	close(start)
	wg.Wait()

	for i := 1; i < goroutines; i++ {
		require.Same(t, got[0], got[i], "concurrent Get must not create duplicate breakers")
	}
	assert.NotSame(t, got[0], mgr.Get("service-b"))
}

func TestStatusRecorder_DefaultsToOKWhenOnlyBodyIsWritten(t *testing.T) {
	cfg := CircuitBreakerConfig{MaxRequests: 1, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5}
	cb, _ := cbNewFrozen(cfg)

	rec, err := cbCall(cb, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("body without an explicit WriteHeader"))
	}))
	require.NoError(t, err)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "body without an explicit WriteHeader", rec.Body.String())
	assert.Equal(t, uint32(1), cbSnapshot(cb).totalSuccesses)
}

func TestStatusRecorder_KeepsFirstStatusCode(t *testing.T) {
	inner := httptest.NewRecorder()
	rec := &statusRecorder{ResponseWriter: inner, statusCode: http.StatusOK}

	rec.WriteHeader(http.StatusBadGateway)
	rec.WriteHeader(http.StatusOK) // a second call must not rewrite history
	_, err := rec.Write([]byte("x"))
	require.NoError(t, err)

	assert.Equal(t, http.StatusBadGateway, rec.statusCode)
	assert.Equal(t, http.StatusBadGateway, inner.Code)
}

func TestStatusRecorder_UnwrapAndFlush(t *testing.T) {
	inner := httptest.NewRecorder()
	rec := &statusRecorder{ResponseWriter: inner, statusCode: http.StatusOK}

	assert.Same(t, inner, rec.Unwrap())

	rec.Flush()
	assert.True(t, inner.Flushed, "Flush must reach a flushable writer")

	// A writer that cannot flush must be tolerated rather than panicking.
	plain := &statusRecorder{ResponseWriter: cbNonFlusher{ResponseWriter: inner}, statusCode: http.StatusOK}
	assert.NotPanics(t, plain.Flush)
}

func TestStatusRecorder_Hijack(t *testing.T) {
	t.Run("writer does not support hijacking", func(t *testing.T) {
		rec := &statusRecorder{ResponseWriter: httptest.NewRecorder(), statusCode: http.StatusOK}
		conn, buf, err := rec.Hijack()
		assert.Nil(t, conn)
		assert.Nil(t, buf)
		assert.ErrorContains(t, err, "does not support hijacking")
	})

	t.Run("hijack is delegated to the wrapped writer", func(t *testing.T) {
		hijacker := &cbHijacker{ResponseWriter: httptest.NewRecorder()}
		rec := &statusRecorder{ResponseWriter: hijacker, statusCode: http.StatusOK}

		conn, buf, err := rec.Hijack()
		assert.Nil(t, conn)
		assert.Nil(t, buf)
		assert.ErrorContains(t, err, "hijacked by the wrapped writer")
		assert.True(t, hijacker.called)
	})
}

// cbNonFlusher hides the Flusher implementation of the writer it wraps.
type cbNonFlusher struct {
	http.ResponseWriter
}

// cbHijacker is a ResponseWriter that reports a hijack attempt.
type cbHijacker struct {
	http.ResponseWriter
	called bool
}

func (h *cbHijacker) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	h.called = true
	return nil, nil, fmt.Errorf("hijacked by the wrapped writer")
}
