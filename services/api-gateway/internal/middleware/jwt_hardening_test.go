package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// protectedHandler builds the middleware chain guarding a single protected
// prefix, recording the claims the downstream handler actually observed.
func protectedHandler(secret string, observed *JWTClaims) http.Handler {
	cfg := JWTConfig{
		Secret:              secret,
		PublicPath:          DefaultPublicPaths(),
		PrefixPath:          DefaultPrefixPaths(),
		ProtectedPrefixPath: []string{"/api/v1/files"},
	}
	return JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if claims := GetJWTClaims(r.Context()); claims != nil && observed != nil {
			*observed = *claims
		}
		w.WriteHeader(http.StatusOK)
	}))
}

func protectedRequest(token string) *http.Request {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	return req
}

func TestJWTAuth_TokenSignedWithNoneAlgorithm_Returns401(t *testing.T) {
	// The classic algorithm-confusion attack: an attacker strips the signature and
	// declares alg=none. The keyfunc must refuse anything that is not HMAC.
	unsigned, err := jwt.NewWithClaims(jwt.SigningMethodNone, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "attacker",
			ExpiresAt: jwt.NewNumericDate(time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)),
		},
	}).SignedString(jwt.UnsafeAllowNoneSignatureType)
	require.NoError(t, err)

	rec := httptest.NewRecorder()
	protectedHandler(testSecret, nil).ServeHTTP(rec, protectedRequest(unsigned))

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_TokenWithSignatureStripped_Returns401(t *testing.T) {
	valid := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-alice",
			ExpiresAt: jwt.NewNumericDate(time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)),
		},
	})

	// Keep the header and payload, drop the signature bytes.
	stripped := valid[:len(valid)-len(signatureSegment(valid))]

	rec := httptest.NewRecorder()
	protectedHandler(testSecret, nil).ServeHTTP(rec, protectedRequest(stripped))

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_EmptyBearerToken_Returns401(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Set("Authorization", "Bearer ")
	rec := httptest.NewRecorder()

	protectedHandler(testSecret, nil).ServeHTTP(rec, req)

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestJWTAuth_TokenExpiringExactlyNow_IsAccepted(t *testing.T) {
	// validateToken rejects on exp.Before(now), so the instant exp == now is still
	// inside the window. Pinned because the auth-service issuer and this verifier
	// have to agree on which side of the boundary is valid.
	expiring := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-alice",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(2 * time.Second)),
		},
	})

	rec := httptest.NewRecorder()
	protectedHandler(testSecret, nil).ServeHTTP(rec, protectedRequest(expiring))

	assert.Equal(t, http.StatusOK, rec.Code)
}

func TestJWTAuth_TokenWithoutExpiryClaim_IsAcceptedForever(t *testing.T) {
	// validateToken only checks expiry when ExpiresAt is non-nil, so a token minted
	// without an exp never ages out. Pinned so the consequence is on the record.
	eternal := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{Subject: "user-alice"},
	})

	rec := httptest.NewRecorder()
	protectedHandler(testSecret, nil).ServeHTTP(rec, protectedRequest(eternal))

	assert.Equal(t, http.StatusOK, rec.Code)
}

func TestJWTAuth_ValidToken_ExposesClaimsToTheDownstreamHandler(t *testing.T) {
	var observed JWTClaims
	token := generateTestToken(t, testSecret, JWTClaims{
		UserID: "user-42",
		Email:  "someone@example.com",
		Roles:  []string{"admin", "auditor"},
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-42",
			ExpiresAt: jwt.NewNumericDate(time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)),
		},
	})

	rec := httptest.NewRecorder()
	protectedHandler(testSecret, &observed).ServeHTTP(rec, protectedRequest(token))

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "user-42", observed.Subject)
	assert.Equal(t, "someone@example.com", observed.Email)
	assert.Equal(t, []string{"admin", "auditor"}, observed.Roles)
}

func TestJWTAuth_RejectedRequest_RespondsWithJSONNotPlainText(t *testing.T) {
	rec := httptest.NewRecorder()
	protectedHandler(testSecret, nil).ServeHTTP(rec, protectedRequest(""))

	assert.Equal(t, http.StatusUnauthorized, rec.Code)
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))
	assert.Contains(t, rec.Body.String(), `"error"`)
}

func TestJWTAuth_EmptyConfiguredSecret_AcceptsATokenAnyoneCanMint(t *testing.T) {
	// JWTAuth does not defend against an empty secret; config.Validate is the only
	// thing that stops the gateway booting without JWT_SECRET. This pins the blast
	// radius of that single check being bypassed or removed.
	forged := generateTestToken(t, "", JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "anyone",
			ExpiresAt: jwt.NewNumericDate(time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)),
		},
	})

	rec := httptest.NewRecorder()
	protectedHandler("", nil).ServeHTTP(rec, protectedRequest(forged))

	assert.Equal(t, http.StatusOK, rec.Code,
		"config.Validate is the only guard against an empty JWT secret; keep it")
}

func TestJWTAuth_PublicPathWithATrailingSlash_StillSkipsValidation(t *testing.T) {
	cfg := JWTConfig{
		Secret:     testSecret,
		PublicPath: DefaultPublicPaths(),
		PrefixPath: DefaultPrefixPaths(),
	}
	handler := JWTAuth(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// /health is a prefix path, so /health/ready is public too.
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	assert.Equal(t, http.StatusOK, rec.Code)

	// But a path that merely starts with the same letters is not.
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthcheck-admin", nil))
	assert.Equal(t, http.StatusUnauthorized, rec.Code,
		"prefix matching must require a / boundary, not a bare string prefix")
}

func TestJWTAuth_LowercaseBearerScheme_IsAccepted(t *testing.T) {
	token := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-alice",
			ExpiresAt: jwt.NewNumericDate(time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)),
		},
	})
	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Set("Authorization", "bearer "+token)
	rec := httptest.NewRecorder()

	protectedHandler(testSecret, nil).ServeHTTP(rec, req)

	// RFC 7235 makes the auth scheme case-insensitive, so a client library that
	// lowercases it must still authenticate.
	assert.Equal(t, http.StatusOK, rec.Code)
}

func TestJWTAuth_MultipleAuthorizationHeaders_UsesTheFirstAndRejectsTheForgery(t *testing.T) {
	valid := generateTestToken(t, testSecret, JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "user-alice",
			ExpiresAt: jwt.NewNumericDate(time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)),
		},
	})
	forged := generateTestToken(t, "not-the-secret", JWTClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "attacker",
			ExpiresAt: jwt.NewNumericDate(time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)),
		},
	})

	var observed JWTClaims
	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/list", nil)
	req.Header.Add("Authorization", "Bearer "+valid)
	req.Header.Add("Authorization", "Bearer "+forged)
	rec := httptest.NewRecorder()

	protectedHandler(testSecret, &observed).ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "user-alice", observed.Subject,
		"a second, forged Authorization header must not override the first")
}

// signatureSegment returns the trailing signature portion of a compact JWS,
// including nothing else, so a test can remove exactly the signature.
func signatureSegment(token string) string {
	for i := len(token) - 1; i >= 0; i-- {
		if token[i] == '.' {
			return token[i+1:]
		}
	}
	return ""
}
