package middleware

import (
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/httpx"
)

// TokenBucket implements a per-IP token bucket rate limiter.
type TokenBucket struct {
	tokens     float64
	maxTokens  float64
	refillRate float64 // tokens per second
	lastRefill time.Time
}

// RateLimiter manages per-IP token buckets.
type RateLimiter struct {
	mu      sync.Mutex
	buckets map[string]*TokenBucket
	rps     int
	now     func() time.Time // for testing
}

// NewRateLimiter creates a rate limiter with the specified requests per second limit.
func NewRateLimiter(rps int) *RateLimiter {
	rl := &RateLimiter{
		buckets: make(map[string]*TokenBucket),
		rps:     rps,
		now:     time.Now,
	}
	go rl.cleanup()
	return rl
}

// Allow checks if a request from the given IP is allowed.
func (rl *RateLimiter) Allow(ip string) bool {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	now := rl.now()
	bucket, exists := rl.buckets[ip]
	if !exists {
		bucket = &TokenBucket{
			tokens:     float64(rl.rps),
			maxTokens:  float64(rl.rps),
			refillRate: float64(rl.rps),
			lastRefill: now,
		}
		rl.buckets[ip] = bucket
	}

	elapsed := now.Sub(bucket.lastRefill).Seconds()
	bucket.tokens += elapsed * bucket.refillRate
	if bucket.tokens > bucket.maxTokens {
		bucket.tokens = bucket.maxTokens
	}
	bucket.lastRefill = now

	if bucket.tokens >= 1 {
		bucket.tokens--
		return true
	}
	return false
}

// Handler returns an HTTP middleware that rate-limits by client IP.
func (rl *RateLimiter) Handler(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ip := extractIP(r)
		if !rl.Allow(ip) {
			w.Header().Set("Retry-After", "1")
			httpx.WriteError(w, http.StatusTooManyRequests, "rate limit exceeded")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func extractIP(r *http.Request) string {
	// chimw.RealIP has already set r.RemoteAddr to the client IP
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

// cleanup periodically removes stale buckets to prevent memory leaks.
func (rl *RateLimiter) cleanup() {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for range ticker.C {
		rl.mu.Lock()
		now := rl.now()
		for ip, bucket := range rl.buckets {
			if now.Sub(bucket.lastRefill) > 10*time.Minute {
				delete(rl.buckets, ip)
			}
		}
		rl.mu.Unlock()
	}
}
