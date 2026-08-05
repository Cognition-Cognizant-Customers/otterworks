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
				if claims == nil || !hasRole(claims.Roles, role) {
					writeJSONError(w, http.StatusForbidden, "insufficient role for this resource")
					return
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}

func hasRole(roles []string, want string) bool {
	for _, r := range roles {
		if strings.EqualFold(r, want) {
			return true
		}
	}
	return false
}
