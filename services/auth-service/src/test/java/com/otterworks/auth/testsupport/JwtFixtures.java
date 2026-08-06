package com.otterworks.auth.testsupport;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.UUID;
import javax.crypto.SecretKey;

/**
 * Hand-crafted JWTs for cases the production {@code JwtTokenProvider} cannot produce: wrong signing
 * key, custom {@code exp}/{@code nbf}, missing claims.
 *
 * <p>Every method is pure and returns a fresh token, so fixtures are never shared or mutated
 * between tests.
 */
public final class JwtFixtures {

  /** Matches {@code jwt.secret} in {@code src/test/resources/application-test.yml}. */
  public static final String TEST_SECRET =
      "test-jwt-secret-otterworks-must-be-at-least-32-bytes-long-for-hmac"; // nosemgrep:

  // generic.secrets.security.detected-jwt-token

  private static final String FOREIGN_SECRET =
      "a-completely-different-secret-that-is-also-long-enough-for-hmac-sha"; // nosemgrep:

  // generic.secrets.security.detected-jwt-token

  private JwtFixtures() {}

  public static SecretKey testKey() {
    return Keys.hmacShaKeyFor(TEST_SECRET.getBytes(StandardCharsets.UTF_8));
  }

  private static SecretKey foreignKey() {
    return Keys.hmacShaKeyFor(FOREIGN_SECRET.getBytes(StandardCharsets.UTF_8));
  }

  /** An access token for a {@code USER} whose lifetime is {@code [issuedAt, expiresAt)}. */
  public static String accessToken(String userId, Instant issuedAt, Instant expiresAt) {
    return accessToken(userId, List.of("USER"), issuedAt, expiresAt);
  }

  /** An access token with the given roles whose lifetime is {@code [issuedAt, expiresAt)}. */
  public static String accessToken(
      String userId, List<String> roles, Instant issuedAt, Instant expiresAt) {
    return Jwts.builder()
        .subject(userId)
        .claim("email", "fixture@otterworks.dev")
        .claim("name", "Fixture User")
        .claim("roles", roles)
        .claim("type", "access")
        .issuedAt(Date.from(issuedAt))
        .expiration(Date.from(expiresAt))
        .signWith(testKey())
        .compact();
  }

  /** An access token that is not valid before {@code notBefore}. */
  public static String accessTokenNotBefore(String userId, Instant notBefore, Instant expiresAt) {
    return Jwts.builder()
        .subject(userId)
        .claim("roles", List.of("USER"))
        .claim("type", "access")
        .issuedAt(Date.from(notBefore.minusSeconds(1)))
        .notBefore(Date.from(notBefore))
        .expiration(Date.from(expiresAt))
        .signWith(testKey())
        .compact();
  }

  public static String accessTokenWithRoles(String userId, List<String> roles) {
    Instant now = Instant.now();
    return Jwts.builder()
        .subject(userId)
        .claim("roles", roles)
        .claim("type", "access")
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(3600)))
        .signWith(testKey())
        .compact();
  }

  public static String accessTokenWithoutRolesClaim(String userId) {
    Instant now = Instant.now();
    return Jwts.builder()
        .subject(userId)
        .claim("type", "access")
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(3600)))
        .signWith(testKey())
        .compact();
  }

  public static String accessTokenSignedWithForeignKey(String userId, List<String> roles) {
    Instant now = Instant.now();
    return Jwts.builder()
        .subject(userId)
        .claim("roles", roles)
        .claim("type", "access")
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(3600)))
        .signWith(foreignKey())
        .compact();
  }

  public static String refreshTokenWithRandomJti(String userId) {
    Instant now = Instant.now();
    return Jwts.builder()
        .subject(userId)
        .id(UUID.randomUUID().toString())
        .claim("type", "refresh")
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(2592000)))
        .signWith(testKey())
        .compact();
  }

  public static String refreshTokenSignedWithForeignKey() {
    Instant now = Instant.now();
    return Jwts.builder()
        .subject(UUID.randomUUID().toString())
        .id(UUID.randomUUID().toString())
        .claim("type", "refresh")
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(2592000)))
        .signWith(foreignKey())
        .compact();
  }

  /** A token with no {@code type} claim at all. */
  public static String tokenWithoutTypeClaim(String userId) {
    Instant now = Instant.now();
    return Jwts.builder()
        .subject(userId)
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(3600)))
        .signWith(testKey())
        .compact();
  }

  /** A token with no {@code sub} claim. */
  public static String tokenWithoutSubject() {
    Instant now = Instant.now();
    return Jwts.builder()
        .claim("type", "access")
        .claim("roles", List.of("ADMIN"))
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(3600)))
        .signWith(testKey())
        .compact();
  }
}
