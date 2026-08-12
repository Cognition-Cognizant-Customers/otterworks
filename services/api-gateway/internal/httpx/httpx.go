// Package httpx provides small shared HTTP helpers used across the gateway:
// JSON response writing and path prefix matching.
package httpx

import (
	"encoding/json"
	"net/http"
	"strings"
)

// WriteJSON writes body as a JSON response with the given status code.
func WriteJSON(w http.ResponseWriter, status int, body interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(body)
}

// WriteError writes a JSON error response of the form {"error": message}.
func WriteError(w http.ResponseWriter, status int, message string) {
	WriteJSON(w, status, map[string]string{"error": message})
}

// HasPathPrefix reports whether path equals prefix or is nested under it
// (i.e. prefix followed by a "/" segment boundary).
func HasPathPrefix(path, prefix string) bool {
	return path == prefix || strings.HasPrefix(path, prefix+"/")
}

// MatchesAnyPrefix reports whether path matches any of the given prefixes
// per HasPathPrefix.
func MatchesAnyPrefix(path string, prefixes []string) bool {
	for _, p := range prefixes {
		if HasPathPrefix(path, p) {
			return true
		}
	}
	return false
}
