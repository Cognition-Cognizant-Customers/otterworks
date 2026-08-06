package com.otterworks.auth.security;

import static org.assertj.core.api.Assertions.*;

import com.otterworks.auth.entity.User;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.Set;
import java.util.UUID;
import javax.crypto.SecretKey;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;

/**
 * Expiry boundary behaviour of {@link JwtTokenProvider}. Assertions are made on the parsed {@code
 * exp}/{@code iat} claims and on parsers driven by a fixed clock, so no test depends on wall-clock
 * time passing.
 */
@TestMethodOrder(MethodOrderer.Random.class)
class JwtTokenExpiryBoundaryTest {

  private static final String SECRET =
      "test-jwt-secret-otterworks-must-be-at-least-32-bytes-long-for-hmac";

  private static final SecretKey KEY =
      Keys.hmacShaKeyFor(SECRET.getBytes(StandardCharsets.UTF_8)); // nosemgrep:

  // java.lang.security.audit.crypto.no-static-initialization-vector

  @Test
  void generateAccessToken_defaultExpiry_expEqualsIatPlus3600Seconds() {
    Claims claims = claimsOf(provider(3600, 2592000).generateAccessToken(user()));

    assertThat(secondsBetweenIatAndExp(claims)).isEqualTo(3600);
  }

  @Test
  void generateAccessToken_customExpiry_expEqualsIatPlusConfiguredExpiry() {
    Claims claims = claimsOf(provider(60, 2592000).generateAccessToken(user()));

    assertThat(secondsBetweenIatAndExp(claims)).isEqualTo(60);
  }

  @Test
  void generateRefreshToken_expEqualsIatPlusConfiguredRefreshExpiry() {
    Claims claims = claimsOf(provider(3600, 2592000).generateRefreshToken(user()));

    assertThat(secondsBetweenIatAndExp(claims)).isEqualTo(2592000);
  }

  @Test
  void generateAccessToken_expiryOfOneSecond_expEqualsIatPlusOneSecond() {
    Claims claims = claimsOf(provider(1, 1).generateAccessToken(user()));

    assertThat(secondsBetweenIatAndExp(claims)).isEqualTo(1);
  }

  @Test
  void isTokenValid_expAlreadyInThePast_returnsFalse() {
    JwtTokenProvider expiredProvider = provider(-3600, -3600);

    assertThat(expiredProvider.isTokenValid(expiredProvider.generateAccessToken(user()))).isFalse();
  }

  @Test
  void validateAndGetClaims_expAlreadyInThePast_throwsExpiredJwtException() {
    JwtTokenProvider expiredProvider = provider(-3600, -3600);
    String token = expiredProvider.generateAccessToken(user());

    assertThatThrownBy(() -> expiredProvider.validateAndGetClaims(token))
        .isInstanceOf(ExpiredJwtException.class);
  }

  @Test
  void validateTokenAndGetUserId_expAlreadyInThePast_throwsExpiredJwtException() {
    JwtTokenProvider expiredProvider = provider(-3600, -3600);
    String token = expiredProvider.generateAccessToken(user());

    assertThatThrownBy(() -> expiredProvider.validateTokenAndGetUserId(token))
        .isInstanceOf(ExpiredJwtException.class);
  }

  /**
   * At an instant exactly equal to {@code exp} the token is still accepted: the verifier treats the
   * expiry boundary as inclusive.
   */
  @Test
  void parseToken_atInstantExactlyEqualToExp_isAccepted() {
    String token = provider(3600, 2592000).generateAccessToken(user());
    Instant exp = claimsOf(token).getExpiration().toInstant();

    Claims claims = parseAt(token, exp);

    assertThat(claims.getExpiration().toInstant()).isEqualTo(exp);
  }

  /** One second past {@code exp} the same token is rejected. */
  @Test
  void parseToken_oneSecondAfterExp_throwsExpiredJwtException() {
    String token = provider(3600, 2592000).generateAccessToken(user());
    Instant exp = claimsOf(token).getExpiration().toInstant();

    assertThatThrownBy(() -> parseAt(token, exp.plusSeconds(1)))
        .isInstanceOf(ExpiredJwtException.class);
  }

  /** One millisecond past {@code exp} is already outside the acceptance window. */
  @Test
  void parseToken_oneMillisecondAfterExp_throwsExpiredJwtException() {
    String token = provider(3600, 2592000).generateAccessToken(user());
    Instant exp = claimsOf(token).getExpiration().toInstant();

    assertThatThrownBy(() -> parseAt(token, exp.plusMillis(1)))
        .isInstanceOf(ExpiredJwtException.class);
  }

  @Test
  void parseToken_oneSecondBeforeExp_isAccepted() {
    String token = provider(3600, 2592000).generateAccessToken(user());
    Instant exp = claimsOf(token).getExpiration().toInstant();

    assertThat(parseAt(token, exp.minusSeconds(1)).getSubject()).isNotBlank();
  }

  private static Claims parseAt(String token, Instant when) {
    return Jwts.parser()
        .verifyWith(KEY)
        .clock(() -> Date.from(when))
        .build()
        .parseSignedClaims(token)
        .getPayload();
  }

  private static long secondsBetweenIatAndExp(Claims claims) {
    return Duration.between(claims.getIssuedAt().toInstant(), claims.getExpiration().toInstant())
        .toSeconds();
  }

  private static Claims claimsOf(String token) {
    return Jwts.parser()
        .verifyWith(KEY)
        .clock(() -> Date.from(Instant.EPOCH))
        .build()
        .parseSignedClaims(token)
        .getPayload();
  }

  private static JwtTokenProvider provider(long accessTokenExpiry, long refreshTokenExpiry) {
    return new JwtTokenProvider(SECRET, accessTokenExpiry, refreshTokenExpiry);
  }

  private static User user() {
    User user = new User();
    user.setId(UUID.randomUUID());
    user.setEmail("expiry@otterworks.dev");
    user.setDisplayName("Expiry User");
    user.setRoles(Set.of(User.Role.USER));
    return user;
  }
}
