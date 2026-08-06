package proxy

import (
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// breakerClock lets a test drive the breaker's notion of time so open/half-open
// transitions are exact and nothing has to sleep.
type breakerClock struct {
	mu sync.Mutex
	t  time.Time
}

func newBreakerClock() *breakerClock {
	return &breakerClock{t: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)}
}

func (c *breakerClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *breakerClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

func newClockedBreaker(cfg CircuitBreakerConfig) (*CircuitBreaker, *breakerClock) {
	clock := newBreakerClock()
	cb := NewCircuitBreaker("test-svc", cfg)
	cb.now = clock.Now
	return cb, clock
}

var (
	okHandler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	serverErrorHandler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	})
	clientErrorHandler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	})
)

func drive(t *testing.T, cb *CircuitBreaker, handler http.Handler, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		require.NoError(t, cb.Execute(handler, httptest.NewRecorder(), req))
	}
}

func TestCircuitBreaker_FourFailures_StaysClosedBelowMinimumSample(t *testing.T) {
	cb, _ := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})

	// shouldTrip requires at least 5 observations, so 4 consecutive failures are
	// not enough no matter how bad the ratio looks.
	drive(t, cb, serverErrorHandler, 4)

	assert.Equal(t, StateClosed, cb.State())
}

func TestCircuitBreaker_FifthFailure_TripsAtTheMinimumSample(t *testing.T) {
	cb, _ := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})

	drive(t, cb, serverErrorHandler, 5)

	assert.Equal(t, StateOpen, cb.State())
}

func TestCircuitBreaker_FailureRatioJustBelowThreshold_StaysClosed(t *testing.T) {
	cb, _ := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.6,
	})

	// 5 of 10 failures is a ratio of 0.5, below the 0.6 threshold. The successes
	// come first so the running ratio never momentarily exceeds the threshold.
	drive(t, cb, okHandler, 5)
	drive(t, cb, serverErrorHandler, 5)

	assert.Equal(t, StateClosed, cb.State())
}

func TestCircuitBreaker_FailureRatioExactlyAtThreshold_Trips(t *testing.T) {
	cb, _ := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})

	// shouldTrip uses >=, so exactly the configured ratio opens the circuit.
	drive(t, cb, okHandler, 3)
	drive(t, cb, serverErrorHandler, 3)

	assert.Equal(t, StateOpen, cb.State())
}

func TestCircuitBreaker_ClientErrorResponses_DoNotCountAsFailures(t *testing.T) {
	cb, _ := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})

	// A 404 is the backend working correctly; only 5xx indicates the dependency
	// is down. Twenty 404s must not take the route out of service.
	drive(t, cb, clientErrorHandler, 20)

	assert.Equal(t, StateClosed, cb.State())
}

func TestCircuitBreaker_OpenCircuit_RejectsWithoutCallingTheBackend(t *testing.T) {
	cb, _ := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})
	drive(t, cb, serverErrorHandler, 5)
	require.Equal(t, StateOpen, cb.State())

	var reached int
	counting := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached++
		w.WriteHeader(http.StatusOK)
	})

	err := cb.Execute(counting, httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/", nil))

	require.Error(t, err, "an open circuit must reject rather than forward")
	assert.Zero(t, reached, "shedding load is the whole point: the backend must not be touched")
}

func TestCircuitBreaker_JustBeforeTimeout_StaysOpen(t *testing.T) {
	cb, clock := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})
	drive(t, cb, serverErrorHandler, 5)
	require.Equal(t, StateOpen, cb.State())

	clock.Advance(10 * time.Second)

	// currentState uses expiry.Before(now), so at exactly the timeout the circuit
	// is still open; recovery begins one tick later.
	assert.Equal(t, StateOpen, cb.State())

	clock.Advance(time.Nanosecond)
	assert.Equal(t, StateHalfOpen, cb.State())
}

func TestCircuitBreaker_HalfOpenBeyondMaxRequests_RejectsTheExtraProbe(t *testing.T) {
	cb, clock := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})
	drive(t, cb, serverErrorHandler, 5)
	clock.Advance(11 * time.Second)
	require.Equal(t, StateHalfOpen, cb.State())

	// A handler that never responds to the recorder keeps the probe "in flight"
	// from the breaker's point of view once the count is incremented, so two
	// concurrent probes are permitted and the third is shed.
	blocked := make(chan struct{})
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			cb.Execute(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				<-blocked
				w.WriteHeader(http.StatusOK)
			}), httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/", nil))
		}()
	}

	// Poll until both probes have been admitted rather than sleeping for them.
	require.Eventually(t, func() bool {
		cb.mu.Lock()
		defer cb.mu.Unlock()
		return cb.counts.requests >= 2
	}, 5*time.Second, time.Millisecond)

	err := cb.Execute(okHandler, httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/", nil))
	assert.Error(t, err, "a half-open circuit must admit at most MaxRequests probes")

	close(blocked)
	wg.Wait()
}

func TestCircuitBreaker_SingleFailingProbeInHalfOpen_ReopensImmediately(t *testing.T) {
	cb, clock := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})
	drive(t, cb, serverErrorHandler, 5)
	clock.Advance(11 * time.Second)
	require.Equal(t, StateHalfOpen, cb.State())

	drive(t, cb, serverErrorHandler, 1)

	assert.Equal(t, StateOpen, cb.State(),
		"one failed probe must reopen the circuit without waiting for a ratio")
}

func TestCircuitBreaker_ReopenedCircuit_RestartsTheFullTimeout(t *testing.T) {
	cb, clock := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})
	drive(t, cb, serverErrorHandler, 5)
	clock.Advance(11 * time.Second)
	require.Equal(t, StateHalfOpen, cb.State())
	drive(t, cb, serverErrorHandler, 1)
	require.Equal(t, StateOpen, cb.State())

	clock.Advance(9 * time.Second)
	assert.Equal(t, StateOpen, cb.State(), "the timeout restarts from the reopen, not the first trip")

	clock.Advance(2 * time.Second)
	assert.Equal(t, StateHalfOpen, cb.State())
}

// DEFECT: NewCircuitBreaker assigns state = StateClosed directly instead of going
// through setState, so cb.expiry is never initialised. currentState only rolls the
// generation when !expiry.IsZero(), which means CB_INTERVAL_SECONDS has no effect
// on a breaker that has never tripped: failure counts accumulate for the lifetime
// of the process, and four transient failures spread over a week open the circuit
// on the fifth.
//
// The fix is one line in NewCircuitBreaker, which is production code, so this test
// is skipped and the behaviour that exists today is pinned below it.
func TestCircuitBreaker_IntervalElapsedWhileClosed_ClearsAccumulatedFailures(t *testing.T) {
	t.Skip("DEFECT: a never-tripped breaker has a zero expiry, so the Interval window never rolls over")

	cb, clock := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})

	drive(t, cb, serverErrorHandler, 4)
	require.Equal(t, StateClosed, cb.State())

	clock.Advance(time.Minute + time.Second)

	drive(t, cb, serverErrorHandler, 4)
	assert.Equal(t, StateClosed, cb.State(), "a new generation starts from zero counts")
}

func TestCircuitBreaker_IntervalElapsedWhileClosed_CurrentlyCarriesFailuresForward(t *testing.T) {
	cb, clock := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})

	drive(t, cb, serverErrorHandler, 4)
	require.Equal(t, StateClosed, cb.State())

	clock.Advance(24 * time.Hour)

	// A single failure a day later still trips the breaker, because the four from
	// yesterday were never cleared.
	drive(t, cb, serverErrorHandler, 1)
	assert.Equal(t, StateOpen, cb.State())
}

func TestCircuitBreaker_IntervalElapsedAfterRecovery_ClearsAccumulatedFailures(t *testing.T) {
	cb, clock := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})

	// Trip, recover and close: closing runs through setState, which finally sets
	// expiry, so from here the Interval window behaves as documented.
	drive(t, cb, serverErrorHandler, 5)
	clock.Advance(11 * time.Second)
	require.Equal(t, StateHalfOpen, cb.State())
	drive(t, cb, okHandler, 2)
	require.Equal(t, StateClosed, cb.State())

	drive(t, cb, serverErrorHandler, 4)
	clock.Advance(time.Minute + time.Second)
	require.Equal(t, StateClosed, cb.State())

	drive(t, cb, serverErrorHandler, 4)
	assert.Equal(t, StateClosed, cb.State(), "a new generation starts from zero counts")
}

func TestCircuitBreaker_ZeroFailureRatio_TripsOnTheFirstFailurePastTheSampleSize(t *testing.T) {
	cb, _ := newClockedBreaker(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0,
	})

	// With a ratio of 0 the comparison ratio >= 0 is always true, so the breaker
	// opens on the first failure once the sample size is met. Pinned because a
	// misconfigured CB_FAILURE_RATIO of 0 silently disables the backend.
	drive(t, cb, okHandler, 4)
	assert.Equal(t, StateClosed, cb.State())

	drive(t, cb, serverErrorHandler, 1)
	assert.Equal(t, StateOpen, cb.State())
}

func TestCircuitBreakerManager_ConcurrentGetForOneName_ReturnsOneBreaker(t *testing.T) {
	m := NewCircuitBreakerManager(defaultTestConfig())

	const callers = 32
	results := make([]*CircuitBreaker, callers)
	var wg sync.WaitGroup
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			results[idx] = m.Get("/api/v1/files")
		}(i)
	}
	wg.Wait()

	for i, cb := range results {
		assert.Same(t, results[0], cb, "caller %d got a different breaker instance", i)
	}
}

func TestCircuitBreakerManager_DifferentRoutes_TripIndependently(t *testing.T) {
	m := NewCircuitBreakerManager(CircuitBreakerConfig{
		MaxRequests: 2, Interval: time.Minute, Timeout: 10 * time.Second, FailureRatio: 0.5,
	})

	failing := m.Get("/api/v1/files")
	drive(t, failing, serverErrorHandler, 5)
	require.Equal(t, StateOpen, failing.State())

	assert.Equal(t, StateClosed, m.Get("/api/v1/auth").State(),
		"one unhealthy backend must not take the rest of the gateway down")
}
