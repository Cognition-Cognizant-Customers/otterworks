package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
)

func serveRealIP(t *testing.T, trusted []string, remoteAddr string, headers map[string]string) (string, http.Header) {
	t.Helper()

	var seenAddr string
	var seenHeaders http.Header
	handler := RealIP(trusted)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seenAddr = extractIP(r)
		seenHeaders = r.Header.Clone()
	}))

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = remoteAddr
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	handler.ServeHTTP(httptest.NewRecorder(), req)
	return seenAddr, seenHeaders
}

func TestRealIP_UntrustedPeerKeepsPeerAddress(t *testing.T) {
	ip, headers := serveRealIP(t, nil, "203.0.113.9:4444", map[string]string{
		"X-Forwarded-For": "10.9.0.1",
		"X-Real-IP":       "10.9.0.2",
	})

	assert.Equal(t, "203.0.113.9", ip, "a client must not choose the address controls key on")
	assert.Empty(t, headers.Get("X-Forwarded-For"), "spoofed forwarding headers are stripped")
	assert.Empty(t, headers.Get("X-Real-IP"))
}

func TestRealIP_TrustedProxyForwardsClient(t *testing.T) {
	ip, _ := serveRealIP(t, []string{"10.0.0.0/8"}, "10.0.1.5:4444", map[string]string{
		"X-Forwarded-For": "198.51.100.7",
	})

	assert.Equal(t, "198.51.100.7", ip)
}

func TestRealIP_TrustedProxyIgnoresPrependedHops(t *testing.T) {
	// The client prepends its own hop; only the address the trusted chain vouches
	// for (the rightmost untrusted one) counts.
	ip, _ := serveRealIP(t, []string{"10.0.0.0/8"}, "10.0.1.5:4444", map[string]string{
		"X-Forwarded-For": "1.2.3.4, 198.51.100.7, 10.0.1.5",
	})

	assert.Equal(t, "198.51.100.7", ip)
}

func TestRealIP_BareTrustedAddress(t *testing.T) {
	ip, _ := serveRealIP(t, []string{"192.0.2.10"}, "192.0.2.10:5555", map[string]string{
		"X-Real-IP": "198.51.100.7",
	})

	assert.Equal(t, "198.51.100.7", ip)
}
