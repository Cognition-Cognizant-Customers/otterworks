package middleware

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const testSecret = "test-secret-key-for-jwt-signing"

func generateTestToken(t *testing.T, secret string, claims JWTClaims) string {
	t.Helper()
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	tokenStr, err := token.SignedString([]byte(secret))
	require.NoError(t, err)
	return tokenStr
}

func TestJWTAuth_PublicPathsSkipValidation(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	publicPaths := []string{
		"/health",
		"/metrics",
		"/api/v1/auth/login",
		"/api/v1/auth/register",
	}

	for _, path := range publicPaths {
		t.Run("public_"+path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, path, nil)
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)
			assert.Equal(t, http.StatusOK, rec.Code, "public path %s should not require auth", path)
		})
	}
}

func TestJWTAuth_SubPathsOfExactMatchRequireAuth(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Sub-paths of exact-match public paths should require auth
	protectedSubPaths := []string{
		"/api/v1/auth/login/callback",
		"/api/v1/auth/register/verify",
	}

	for _, path := range protectedSubPaths {
		t.Run("protected_"+path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, path, nil)
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)
			assert.Equal(t, http.StatusUnauthorized, rec.Code, "sub-path %s should require auth", path)
		})
	}

	// Sub-paths of prefix-match paths should skip auth
	prefixSubPaths := []string{
		"/health/ready",
		"/metrics/prometheus",
	}

	for _, path := range prefixSubPaths {
		t.Run("prefix_public_"+path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, path, nil)
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)
			assert.Equal(t, http.StatusOK, rec.Code, "prefix path %s should not require auth", path)
		})
	}
}

func TestJWTAuth_MissingToken(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_UnmatchedProtectedPrefixSkipsValidation(t *testing.T) {
	cfg := JWTConfig{
		Secret:              testSecret,
		PublicPath:          DefaultPublicPaths(),
		PrefixPath:          DefaultPrefixPaths(),
		ProtectedPrefixPath: []string{"/api/v1/files"},
	}

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/does-not-exist", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusNotFound, rec.Code)
}

func TestJWTAuth_MatchedProtectedPrefixRequiresAuth(t *testing.T) {
	cfg := JWTConfig{
		Secret:              testSecret,
		PublicPath:          DefaultPublicPaths(),
		PrefixPath:          DefaultPrefixPaths(),
		ProtectedPrefixPath: []string{"/api/v1/files"},
	}

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_InvalidToken(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Set("Authorization", "Bearer invalid-token-string")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_ValidToken(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}

	claims := JWTClaims{
		UserID: "user-123",
		Email:  "test@otterworks.dev",
		Roles:  []string{"user"},
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(1 * time.Hour)),
			IssuedAt:  jwt.NewNumericDate(time.Now()),
			Subject:   "user-123",
		},
	}

	tokenStr := generateTestToken(t, testSecret, claims)

	var capturedClaims *JWTClaims
	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedClaims = GetJWTClaims(r.Context())
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Set("Authorization", "Bearer "+tokenStr)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusOK, rec.Code)
	require.NotNil(t, capturedClaims)
	assert.Equal(t, "user-123", capturedClaims.UserID)
	assert.Equal(t, "test@otterworks.dev", capturedClaims.Email)
}

func TestJWTAuth_ExpiredToken(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}

	claims := JWTClaims{
		UserID: "user-123",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(-1 * time.Hour)),
			IssuedAt:  jwt.NewNumericDate(time.Now().Add(-2 * time.Hour)),
		},
	}

	tokenStr := generateTestToken(t, testSecret, claims)

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Set("Authorization", "Bearer "+tokenStr)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_WrongSecret(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}

	claims := JWTClaims{
		UserID: "user-123",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(1 * time.Hour)),
		},
	}

	tokenStr := generateTestToken(t, "wrong-secret", claims)

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Set("Authorization", "Bearer "+tokenStr)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_MalformedAuthHeader(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}

	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	tests := []struct {
		name   string
		header string
	}{
		{"no Bearer prefix", "token-without-bearer"},
		{"Basic auth", "Basic dXNlcjpwYXNz"},
		{"empty Bearer", "Bearer "},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
			req.Header.Set("Authorization", tt.header)
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)
			assert.Equal(t, http.StatusUnauthorized, rec.Code)
		})
	}
}

func TestExtractBearerToken(t *testing.T) {
	tests := []struct {
		name     string
		header   string
		expected string
	}{
		{"valid Bearer", "Bearer abc123", "abc123"},
		{"case insensitive", "bearer abc123", "abc123"},
		{"no header", "", ""},
		{"no Bearer prefix", "abc123", ""},
		{"Basic auth", "Basic dXNlcjpwYXNz", ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/", nil)
			if tt.header != "" {
				req.Header.Set("Authorization", tt.header)
			}
			result := extractBearerToken(req)
			assert.Equal(t, tt.expected, result)
		})
	}
}

func TestGetJWTClaims_NilContext(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	claims := GetJWTClaims(req.Context())
	assert.Nil(t, claims)
}

// ---------------------------------------------------------------------------
// WP-03: JWT negatives and boundaries.
//
// Everything below is additive; nothing above this line was modified.
// ---------------------------------------------------------------------------

// protectedPath is a path that always requires authentication under the
// default config used by these tests.
const protectedPath = "/api/v1/files/list"

func defaultJWTConfig() JWTConfig {
	return JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}
}

// authHandler returns a handler chain that records whether the inner handler
// ran and with which claims.
func authHandler(cfg JWTConfig, seen **JWTClaims, reached *bool) http.Handler {
	return JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*reached = true
		*seen = GetJWTClaims(r.Context())
		w.WriteHeader(http.StatusOK)
	}))
}

// serveWithAuth sends a request carrying the given Authorization header value
// (skipped entirely when authHeader is the sentinel "").
func serveWithAuth(t *testing.T, cfg JWTConfig, authHeader string) (*httptest.ResponseRecorder, *JWTClaims, bool) {
	t.Helper()
	var seen *JWTClaims
	var reached bool
	h := authHandler(cfg, &seen, &reached)

	req := httptest.NewRequest(http.MethodGet, protectedPath, nil)
	if authHeader != "" {
		req.Header.Set("Authorization", authHeader)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec, seen, reached
}

func signWith(t *testing.T, method jwt.SigningMethod, secret string, claims JWTClaims) string {
	t.Helper()
	tokenStr, err := jwt.NewWithClaims(method, claims).SignedString([]byte(secret))
	require.NoError(t, err)
	return tokenStr
}

// --- Authorization header shapes -------------------------------------------

func TestJWTAuth_AuthorizationHeaderShapes(t *testing.T) {
	valid := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-1",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	cases := []struct {
		name       string
		header     string
		expectCode int
	}{
		{"header absent", "", http.StatusUnauthorized},
		{"header present but empty", " ", http.StatusUnauthorized},
		{"scheme only, no space", "Bearer", http.StatusUnauthorized},
		{"scheme with trailing space only", "Bearer ", http.StatusUnauthorized},
		{"wrong scheme", "Token " + valid, http.StatusUnauthorized},
		{"bearer misspelled", "Bearerr " + valid, http.StatusUnauthorized},
		{"double space before token", "Bearer  " + valid, http.StatusUnauthorized},
		{"token without scheme", valid, http.StatusUnauthorized},
		{"lowercase scheme accepted", "bearer " + valid, http.StatusOK},
		{"uppercase scheme accepted", "BEARER " + valid, http.StatusOK},
		{"canonical scheme accepted", "Bearer " + valid, http.StatusOK},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rec, _, reached := serveWithAuth(t, defaultJWTConfig(), tc.header)
			assert.Equal(t, tc.expectCode, rec.Code)
			assert.Equal(t, tc.expectCode == http.StatusOK, reached,
				"the protected handler must run only on success")
		})
	}
}

func TestJWTAuth_RejectionBodyIsJSONWithErrorField(t *testing.T) {
	rec, _, _ := serveWithAuth(t, defaultJWTConfig(), "")

	require.Equal(t, http.StatusUnauthorized, rec.Code)
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))

	var body map[string]string
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	assert.Equal(t, "missing or invalid authorization header", body["error"])
}

// --- signature / algorithm negatives ---------------------------------------

func TestJWTAuth_AlgNoneIsRejected(t *testing.T) {
	claims := JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "attacker",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	}
	unsigned, err := jwt.NewWithClaims(jwt.SigningMethodNone, claims).
		SignedString(jwt.UnsafeAllowNoneSignatureType)
	require.NoError(t, err)

	rec, _, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+unsigned)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
	assert.False(t, reached)
}

func TestJWTAuth_TamperedSignatureIsRejected(t *testing.T) {
	valid := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-1",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	// Flip the last character of the signature segment.
	last := valid[len(valid)-1]
	replacement := byte('A')
	if last == 'A' {
		replacement = 'B'
	}
	tampered := valid[:len(valid)-1] + string(replacement)

	rec, _, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+tampered)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
	assert.False(t, reached)
}

func TestJWTAuth_StructurallyInvalidTokensAreRejected(t *testing.T) {
	cases := []struct {
		name  string
		token string
	}{
		{"not a JWT", "not-a-jwt"},
		{"two segments only", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0"},
		{"four segments", "a.b.c.d"},
		{"empty segments", ".."},
		{"non-base64 payload", "eyJhbGciOiJIUzI1NiJ9.!!!.sig"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rec, _, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+tc.token)
			assert.Equal(t, http.StatusUnauthorized, rec.Code)
			assert.False(t, reached)
		})
	}
}

// Any HMAC variant signed with the shared secret is accepted: the key function
// only pins the algorithm *family*, not HS256 specifically.
func TestJWTAuth_OtherHMACAlgorithmsAreAccepted(t *testing.T) {
	for _, method := range []jwt.SigningMethod{jwt.SigningMethodHS256, jwt.SigningMethodHS384, jwt.SigningMethodHS512} {
		t.Run(method.Alg(), func(t *testing.T) {
			token := signWith(t, method, testSecret, JWTClaims{
				RegisteredClaims: jwt.RegisteredClaims{
					Subject:   "user-1",
					ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
				},
			})
			rec, _, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)
			assert.Equal(t, http.StatusOK, rec.Code)
			assert.True(t, reached)
		})
	}
}

// With an empty configured secret, a token signed with an empty key validates.
// main() refuses to boot in that state (Config.Validate), so this pins the
// middleware's own (absent) defence rather than reporting a live exposure.
func TestJWTAuth_EmptySecretAcceptsTokensSignedWithEmptyKey(t *testing.T) {
	cfg := defaultJWTConfig()
	cfg.Secret = ""

	token := generateTestToken(t, "", JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "anyone",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	rec, _, reached := serveWithAuth(t, cfg, "Bearer "+token)

	assert.Equal(t, http.StatusOK, rec.Code, "current behavior; Config.Validate() is what prevents an empty secret in production")
	assert.True(t, reached)
}

// --- time-based claims: exp / nbf / iat boundaries --------------------------

func TestJWTAuth_ExpirationBoundary(t *testing.T) {
	cases := []struct {
		name       string
		exp        time.Duration // relative to token creation
		expectCode int
	}{
		{"expired one hour ago", -time.Hour, http.StatusUnauthorized},
		{"expired one second ago", -time.Second, http.StatusUnauthorized},
		// exp == the instant the token was minted: validation happens strictly
		// later, so the token is already expired. No sleeps involved.
		{"expires at the creation instant", 0, http.StatusUnauthorized},
		{"expires in one second", time.Second, http.StatusOK},
		{"expires in one hour", time.Hour, http.StatusOK},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			token := generateTestToken(t, testSecret, JWTClaims{
				RegisteredClaims: jwt.RegisteredClaims{
					Subject:   "user-1",
					ExpiresAt: jwt.NewNumericDate(time.Now().Add(tc.exp)),
				},
			})
			rec, _, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)
			assert.Equal(t, tc.expectCode, rec.Code)
			assert.Equal(t, tc.expectCode == http.StatusOK, reached)
		})
	}
}

func TestJWTAuth_NotBeforeBoundary(t *testing.T) {
	cases := []struct {
		name       string
		nbf        time.Duration
		expectCode int
	}{
		{"nbf one hour in the future", time.Hour, http.StatusUnauthorized},
		{"nbf one second in the future", time.Second, http.StatusUnauthorized},
		{"nbf at the creation instant", 0, http.StatusOK},
		{"nbf one second in the past", -time.Second, http.StatusOK},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			token := generateTestToken(t, testSecret, JWTClaims{
				RegisteredClaims: jwt.RegisteredClaims{
					Subject:   "user-1",
					NotBefore: jwt.NewNumericDate(time.Now().Add(tc.nbf)),
					ExpiresAt: jwt.NewNumericDate(time.Now().Add(2 * time.Hour)),
				},
			})
			rec, _, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)
			assert.Equal(t, tc.expectCode, rec.Code)
			assert.Equal(t, tc.expectCode == http.StatusOK, reached)
		})
	}
}

// A token with no exp at all never expires. Pinned as current behavior.
func TestJWTAuth_TokenWithoutExpiryIsAccepted(t *testing.T) {
	token := generateTestToken(t, testSecret, JWTClaims{
		UserID:           "user-1",
		RegisteredClaims: jwt.RegisteredClaims{Subject: "user-1"},
	})

	rec, claims, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.True(t, reached)
	require.NotNil(t, claims)
	assert.Nil(t, claims.ExpiresAt)
}

// An issued-at far in the future is not rejected (iat is not validated).
func TestJWTAuth_FutureIssuedAtIsAccepted(t *testing.T) {
	token := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-1",
			IssuedAt:  jwt.NewNumericDate(time.Now().Add(24 * time.Hour)),
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(48 * time.Hour)),
		},
	})

	rec, _, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.True(t, reached)
}

// --- identity claims: iss / aud / sub ---------------------------------------

// FINDING (documents current behavior): validateToken never checks the issuer,
// so a token minted by any system that happens to share the HMAC secret is
// accepted. See TestJWTAuth_WrongIssuerShouldBeRejected for the intended
// behavior.
func TestJWTAuth_WrongIssuerIsAccepted_FINDING(t *testing.T) {
	token := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    "https://evil.example.com",
			Subject:   "user-1",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	rec, claims, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)

	assert.Equal(t, http.StatusOK, rec.Code, "current behavior: the issuer claim is not validated")
	assert.True(t, reached)
	require.NotNil(t, claims)
	assert.Equal(t, "https://evil.example.com", claims.Issuer)
}

func TestJWTAuth_WrongIssuerShouldBeRejected(t *testing.T) {
	t.Skip("DEFECT: the gateway does not validate the `iss` claim (no jwt.WithIssuer option and no " +
		"expected-issuer config), so any token signed with the shared secret is accepted regardless of " +
		"who minted it. Fixing it is a production change to internal/middleware/jwt.go plus new config, " +
		"out of scope for this coverage PR.")

	token := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    "https://evil.example.com",
			Subject:   "user-1",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	rec, _, _ := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)
	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

// FINDING: the audience claim is not validated either.
func TestJWTAuth_WrongAudienceIsAccepted_FINDING(t *testing.T) {
	token := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Audience:  jwt.ClaimStrings{"some-other-service"},
			Subject:   "user-1",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	rec, _, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)

	assert.Equal(t, http.StatusOK, rec.Code, "current behavior: the audience claim is not validated")
	assert.True(t, reached)
}

// FINDING: a token with neither `sub` nor `user_id` authenticates. The proxy
// director then has no identity to stamp onto X-User-ID, which is what
// internal/proxy/router_test.go's
// TestProxy_SpoofedXUserIDSurvivesWhenClaimsCarryNoUserID_SECURITY_FINDING
// exploits.
func TestJWTAuth_MissingSubjectIsAccepted_FINDING(t *testing.T) {
	token := generateTestToken(t, testSecret, JWTClaims{
		Email: "otter@otterworks.dev",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	rec, claims, reached := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)

	assert.Equal(t, http.StatusOK, rec.Code, "current behavior: an anonymous token is accepted")
	assert.True(t, reached)
	require.NotNil(t, claims)
	assert.Empty(t, claims.Subject)
	assert.Empty(t, claims.UserID)
}

func TestJWTAuth_MissingSubjectShouldBeRejected(t *testing.T) {
	t.Skip("DEFECT: a token carrying neither `sub` nor `user_id` passes validation, so the request is " +
		"proxied with no gateway-derived identity. Rejecting it is a production change to " +
		"internal/middleware/jwt.go, out of scope for this coverage PR.")

	token := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour))},
	})

	rec, _, _ := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)
	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_ClaimsAreExposedToDownstreamHandler(t *testing.T) {
	token := generateTestToken(t, testSecret, JWTClaims{
		UserID: "custom-id",
		Email:  "otter@otterworks.dev",
		Roles:  []string{"user", "admin"},
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "sub-id",
			Issuer:    "otterworks-auth",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	})

	rec, claims, _ := serveWithAuth(t, defaultJWTConfig(), "Bearer "+token)

	require.Equal(t, http.StatusOK, rec.Code)
	require.NotNil(t, claims)
	assert.Equal(t, "custom-id", claims.UserID)
	assert.Equal(t, "sub-id", claims.Subject)
	assert.Equal(t, []string{"user", "admin"}, claims.Roles)
}

// --- path classification ----------------------------------------------------

func TestIsPublicPath(t *testing.T) {
	exact := map[string]bool{"/api/v1/auth/login": true, "/api/v1/auth/register": true}
	prefixes := DefaultPrefixPaths()

	cases := []struct {
		path     string
		expected bool
	}{
		{"/api/v1/auth/login", true},
		{"/api/v1/auth/register", true},
		{"/api/v1/auth/login/", false},
		{"/api/v1/auth/login/extra", false},
		{"/API/V1/AUTH/LOGIN", false},
		{"/health", true},
		{"/health/", true},
		{"/health/ready", true},
		{"/healthz", false},
		{"/metrics", true},
		{"/socket.io", true},
		{"/socket.io/anything", true},
		{"/socket.iox", false},
		{"/api/v1/files", false},
		{"", false},
		{"/", false},
	}

	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			assert.Equal(t, tc.expected, isPublicPath(tc.path, exact, prefixes))
		})
	}
}

func TestIsProtectedPath(t *testing.T) {
	prefixes := []string{"/api/v1/files", "/api/v1/documents"}

	cases := []struct {
		name              string
		path              string
		protectedPrefixes []string
		expected          bool
	}{
		{"exact prefix", "/api/v1/files", prefixes, true},
		{"sub path", "/api/v1/files/abc", prefixes, true},
		{"trailing slash", "/api/v1/files/", prefixes, true},
		{"longer sibling segment", "/api/v1/filesystem", prefixes, false},
		{"unrelated path", "/api/v1/other", prefixes, false},
		{"empty list protects everything", "/anything", nil, true},
		{"empty list protects the root", "/", []string{}, true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			assert.Equal(t, tc.expected, isProtectedPath(tc.path, tc.protectedPrefixes))
		})
	}
}

func TestJWTAuth_PublicPathIgnoresAnInvalidToken(t *testing.T) {
	var reached bool
	var seen *JWTClaims
	h := authHandler(defaultJWTConfig(), &seen, &reached)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", nil)
	req.Header.Set("Authorization", "Bearer garbage")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.True(t, reached)
	assert.Nil(t, seen, "public paths never populate claims")
}

func TestValidateToken_ReturnsErrorsDirectly(t *testing.T) {
	t.Run("expired", func(t *testing.T) {
		token := generateTestToken(t, testSecret, JWTClaims{
			RegisteredClaims: jwt.RegisteredClaims{ExpiresAt: jwt.NewNumericDate(time.Now().Add(-time.Hour))},
		})
		claims, err := validateToken(token, testSecret)
		require.Error(t, err)
		assert.Nil(t, claims)
	})

	t.Run("valid", func(t *testing.T) {
		token := generateTestToken(t, testSecret, JWTClaims{
			RegisteredClaims: jwt.RegisteredClaims{
				Subject:   "user-1",
				ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
			},
		})
		claims, err := validateToken(token, testSecret)
		require.NoError(t, err)
		require.NotNil(t, claims)
		assert.Equal(t, "user-1", claims.Subject)
	})

	t.Run("empty token string", func(t *testing.T) {
		claims, err := validateToken("", testSecret)
		require.Error(t, err)
		assert.Nil(t, claims)
	})
}
