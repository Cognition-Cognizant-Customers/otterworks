package middleware

import (
	"net"
	"net/http"
	"strings"
)

// forwardedHeaders are the client-settable headers that describe the hop in front of
// the gateway: who the caller is, and how they reached the edge.
var forwardedHeaders = []string{"X-Forwarded-For", "X-Real-IP", "True-Client-IP", "X-Forwarded-Proto"}

// RealIP returns middleware that rewrites r.RemoteAddr from the forwarding headers
// only when the immediate peer is a trusted proxy. Anything else keeps the peer
// address and loses its forwarding headers entirely, so a client cannot choose the
// identity that per-IP controls key on, nor claim a scheme it did not use.
func RealIP(trustedProxies []string) func(http.Handler) http.Handler {
	trusted := parseCIDRs(trustedProxies)

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			peer := peerIP(r)
			if !ipInAny(peer, trusted) {
				for _, h := range forwardedHeaders {
					r.Header.Del(h)
				}
				next.ServeHTTP(w, r)
				return
			}
			if client := clientFromForwarded(r, trusted); client != "" {
				r.RemoteAddr = net.JoinHostPort(client, "0")
			}
			next.ServeHTTP(w, r)
		})
	}
}

// parseCIDRs accepts CIDR blocks and bare addresses.
func parseCIDRs(entries []string) []*net.IPNet {
	nets := make([]*net.IPNet, 0, len(entries))
	for _, entry := range entries {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		if _, network, err := net.ParseCIDR(entry); err == nil {
			nets = append(nets, network)
			continue
		}
		if ip := net.ParseIP(entry); ip != nil {
			bits := 32
			if ip.To4() == nil {
				bits = 128
			}
			nets = append(nets, &net.IPNet{IP: ip, Mask: net.CIDRMask(bits, bits)})
		}
	}
	return nets
}

func peerIP(r *http.Request) net.IP {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	return net.ParseIP(host)
}

func ipInAny(ip net.IP, nets []*net.IPNet) bool {
	if ip == nil {
		return false
	}
	for _, n := range nets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

// clientFromForwarded walks X-Forwarded-For from right to left and returns the first
// address that is not itself a trusted proxy: the last hop the trusted chain vouches
// for. Addresses further left are attacker-controlled and ignored.
func clientFromForwarded(r *http.Request, trusted []*net.IPNet) string {
	var chain []string
	for _, value := range r.Header.Values("X-Forwarded-For") {
		for _, part := range strings.Split(value, ",") {
			if part = strings.TrimSpace(part); part != "" {
				chain = append(chain, part)
			}
		}
	}
	for i := len(chain) - 1; i >= 0; i-- {
		ip := net.ParseIP(chain[i])
		if ip == nil {
			continue
		}
		if !ipInAny(ip, trusted) {
			return ip.String()
		}
	}
	if realIP := net.ParseIP(strings.TrimSpace(r.Header.Get("X-Real-IP"))); realIP != nil {
		return realIP.String()
	}
	return ""
}
