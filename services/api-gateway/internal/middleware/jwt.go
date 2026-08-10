package middleware

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type jwtClaimsKey struct{}

// JWTClaims represents the claims extracted from a validated JWT.
type JWTClaims struct {
	UserID string   `json:"user_id,omitempty"`
	Email  string   `json:"email,omitempty"`
	Roles  []string `json:"roles,omitempty"`
	jwt.RegisteredClaims
}

// JWTConfig holds configuration for JWT validation middleware.
type JWTConfig struct {
	Secret              string
	PublicPath          []string // exact paths that skip JWT validation
	PrefixPath          []string // prefix paths that skip JWT validation (e.g. /health)
	ProtectedPrefixPath []string // route prefixes that require JWT validation; empty means all non-public paths
}

// DefaultPublicPaths returns the default set of exact-match paths that skip JWT validation.
func DefaultPublicPaths() []string {
	return []string{
		"/api/v1/auth/login",
		"/api/v1/auth/register",
	}
}

// DefaultPrefixPaths returns paths where any sub-path also skips JWT validation.
func DefaultPrefixPaths() []string {
	return []string{
		"/health",
		"/socket.io",
	}
}

// JWTAuth returns middleware that validates JWT tokens on protected routes.
func JWTAuth(cfg JWTConfig) func(http.Handler) http.Handler {
	exactPaths := make(map[string]bool, len(cfg.PublicPath))
	for _, p := range cfg.PublicPath {
		exactPaths[p] = true
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Skip JWT validation for public paths
			if isPublicPath(r.URL.Path, exactPaths, cfg.PrefixPath) {
				next.ServeHTTP(w, r)
				return
			}
			if !isProtectedPath(r.URL.Path, cfg.ProtectedPrefixPath) {
				next.ServeHTTP(w, r)
				return
			}

			tokenStr := extractBearerToken(r)
			if tokenStr == "" {
				writeJSONError(w, http.StatusUnauthorized, "missing or invalid authorization header")
				return
			}

			claims, err := validateToken(tokenStr, cfg.Secret)
			if err != nil {
				writeJSONError(w, http.StatusUnauthorized, fmt.Sprintf("invalid token: %v", err))
				return
			}

			ctx := context.WithValue(r.Context(), jwtClaimsKey{}, claims)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// GetJWTClaims retrieves JWT claims from the request context.
func GetJWTClaims(ctx context.Context) *JWTClaims {
	if claims, ok := ctx.Value(jwtClaimsKey{}).(*JWTClaims); ok {
		return claims
	}
	return nil
}

func extractBearerToken(r *http.Request) string {
	auth := r.Header.Get("Authorization")
	if auth == "" {
		return ""
	}
	parts := strings.SplitN(auth, " ", 2)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "bearer") {
		return ""
	}
	return parts[1]
}

func validateToken(tokenStr, secret string) (*JWTClaims, error) {
	claims := &JWTClaims{}
	token, err := jwt.ParseWithClaims(tokenStr, claims, func(token *jwt.Token) (interface{}, error) {
		if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
		}
		return []byte(secret), nil
	})
	if err != nil {
		return nil, err
	}
	if !token.Valid {
		return nil, fmt.Errorf("token is not valid")
	}

	// Check expiration
	if claims.ExpiresAt != nil && claims.ExpiresAt.Time.Before(time.Now()) {
		return nil, fmt.Errorf("token has expired")
	}

	return claims, nil
}

func isPublicPath(path string, exactPaths map[string]bool, prefixPaths []string) bool {
	// Exact match for API paths (e.g. /api/v1/auth/login)
	if exactPaths[path] {
		return true
	}
	// Prefix match only for operational paths (e.g. /health)
	for _, p := range prefixPaths {
		if path == p || strings.HasPrefix(path, p+"/") {
			return true
		}
	}
	return false
}

func isProtectedPath(path string, protectedPrefixes []string) bool {
	if len(protectedPrefixes) == 0 {
		return true
	}
	for _, p := range protectedPrefixes {
		if path == p || strings.HasPrefix(path, p+"/") {
			return true
		}
	}
	return false
}

func writeJSONError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{
		"error": message,
	})
}
