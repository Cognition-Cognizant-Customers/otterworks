package middleware

import (
	"net/http"
	"strings"
)

// RequireRole returns middleware that rejects requests under pathPrefix
// unless the validated JWT claims include the given role.
func RequireRole(pathPrefix, role string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == pathPrefix || strings.HasPrefix(r.URL.Path, pathPrefix+"/") {
				claims := GetJWTClaims(r.Context())
				if claims == nil || !hasRole(claimRoles(claims), role) {
					writeJSONError(w, http.StatusForbidden, "insufficient role for this resource")
					return
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}

// claimRoles collects roles from both the plural "roles" claim (auth-service
// tokens) and the singular "role" claim (admin-dashboard tokens).
func claimRoles(claims *JWTClaims) []string {
	roles := claims.Roles
	if claims.Role != "" {
		roles = append(roles, claims.Role)
	}
	return roles
}

func hasRole(roles []string, want string) bool {
	for _, r := range roles {
		if strings.EqualFold(r, want) {
			return true
		}
	}
	return false
}
