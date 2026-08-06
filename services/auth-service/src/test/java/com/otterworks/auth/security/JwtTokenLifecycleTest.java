package com.otterworks.auth.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.otterworks.auth.entity.User;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.Jws;
import io.jsonwebtoken.JwtBuilder;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.PrematureJwtException;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Date;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.function.UnaryOperator;
import javax.crypto.SecretKey;
import org.junit.jupiter.api.Test;

/**
 * Token lifecycle edges for {@link JwtTokenProvider}: {@code exp}/{@code nbf} boundaries, signature
 * and algorithm negatives, and access/refresh type confusion.
 *
 * <p>The exact-second boundary trio is evaluated against a <b>fixed clock</b> injected into a jjwt
 * parser configured exactly like the one inside {@code JwtTokenProvider} (same key, same defaults).
 * No test sleeps or depends on how long the suite takes to run.
 *
 * <p>FINDING (no production change made): {@code JwtTokenProvider} builds its parser with {@code
 * Jwts.parser().verifyWith(key)} and reads the wall clock through {@code Instant.now()}, with no
 * injectable {@code Clock} and no {@code clockSkewSeconds(...)} allowance. That means (a) the exact
 * expiry boundary is not reachable through the production API in a deterministic test, and (b) a
 * client whose clock is a second fast/slow is rejected outright, where 30-60s of skew tolerance is
 * the usual default.
 */
class JwtTokenLifecycleTest {

  private static final String SECRET =
      "test-jwt-secret-otterworks-must-be-at-least-32-bytes-long-for-hmac"; // nosemgrep:
  // generic.secrets.security.detected-generic-secret
  private static final String OTHER_SECRET =
      "another-jwt-secret-otterworks-also-at-least-32-bytes-long-for-hmac"; // nosemgrep:
  // generic.secrets.security.detected-generic-secret
  private static final long ACCESS_TOKEN_EXPIRY_SECONDS = 3600L;
  private static final long REFRESH_TOKEN_EXPIRY_SECONDS = 2592000L;

  private final SecretKey key = Keys.hmacShaKeyFor(SECRET.getBytes(StandardCharsets.UTF_8));
  private final JwtTokenProvider provider =
      new JwtTokenProvider(
          SECRET, ACCESS_TOKEN_EXPIRY_SECONDS, REFRESH_TOKEN_EXPIRY_SECONDS); // nosemgrep:

  // java.lang.security.audit.crypto.no-static-initialization-vector

  // ---------------------------------------------------------------- exp boundary trio

  @Test
  void accessToken_isValidOneSecondBeforeExpiry() {
    String token = provider.generateAccessToken(user(User.Role.USER));
    Instant expiry = expiryOf(token);

    assertThatCode(() -> parseAt(token, expiry.minusSeconds(1))).doesNotThrowAnyException();
  }

  @Test
  void accessToken_isStillValidAtTheExactExpiryInstant() {
    // Pins jjwt's inclusive comparison: a token is expired only once "now" is strictly after
    // exp, so exp itself is the last valid instant.
    String token = provider.generateAccessToken(user(User.Role.USER));
    Instant expiry = expiryOf(token);

    assertThatCode(() -> parseAt(token, expiry)).doesNotThrowAnyException();
  }

  @Test
  void accessToken_isRejectedOneSecondAfterExpiry() {
    String token = provider.generateAccessToken(user(User.Role.USER));
    Instant expiry = expiryOf(token);

    assertThatThrownBy(() -> parseAt(token, expiry.plusSeconds(1)))
        .isInstanceOf(ExpiredJwtException.class);
  }

  @Test
  void accessToken_expIsExactlyIssuedAtPlusTheConfiguredTtl() {
    String token = provider.generateAccessToken(user(User.Role.USER));
    Claims claims = provider.validateAndGetClaims(token);

    long ttl =
        claims.getExpiration().toInstant().getEpochSecond()
            - claims.getIssuedAt().toInstant().getEpochSecond();
    assertThat(ttl).isEqualTo(ACCESS_TOKEN_EXPIRY_SECONDS);
  }

  @Test
  void refreshToken_expIsExactlyIssuedAtPlusTheConfiguredTtl() {
    String token = provider.generateRefreshToken(user(User.Role.USER));
    Claims claims = provider.validateAndGetClaims(token);

    long ttl =
        claims.getExpiration().toInstant().getEpochSecond()
            - claims.getIssuedAt().toInstant().getEpochSecond();
    assertThat(ttl).isEqualTo(REFRESH_TOKEN_EXPIRY_SECONDS);
  }

  @Test
  void expiredToken_isRejectedWithNoClockSkewAllowance() {
    // A token that expired one second ago is rejected outright: the parser allows zero skew.
    String token = signed(claims -> claims.subject(UUID.randomUUID().toString()), -1, null);

    assertThat(provider.isTokenValid(token)).isFalse();
  }

  // ---------------------------------------------------------------- nbf boundary trio

  @Test
  void notBeforeToken_isRejectedOneSecondBeforeItBecomesValid() {
    Instant notBefore = Instant.now().plus(1, ChronoUnit.HOURS).truncatedTo(ChronoUnit.SECONDS);
    String token = notBeforeToken(notBefore);

    assertThatThrownBy(() -> parseAt(token, notBefore.minusSeconds(1)))
        .isInstanceOf(PrematureJwtException.class);
  }

  @Test
  void notBeforeToken_isValidAtTheExactNotBeforeInstant() {
    Instant notBefore = Instant.now().plus(1, ChronoUnit.HOURS).truncatedTo(ChronoUnit.SECONDS);
    String token = notBeforeToken(notBefore);

    assertThatCode(() -> parseAt(token, notBefore)).doesNotThrowAnyException();
  }

  @Test
  void notBeforeToken_isValidOneSecondAfterItBecomesValid() {
    Instant notBefore = Instant.now().plus(1, ChronoUnit.HOURS).truncatedTo(ChronoUnit.SECONDS);
    String token = notBeforeToken(notBefore);

    assertThatCode(() -> parseAt(token, notBefore.plusSeconds(1))).doesNotThrowAnyException();
  }

  @Test
  void notBeforeInTheFuture_isRejectedByTheProvider() {
    String token =
        signed(
            claims -> claims.subject(UUID.randomUUID().toString()),
            3600,
            Instant.now().plus(1, ChronoUnit.HOURS));

    assertThat(provider.isTokenValid(token)).isFalse();
  }

  @Test
  void providerDoesNotEmitNotBeforeClaims() {
    // Documents today's shape: neither token carries nbf, so nbf handling only matters for
    // tokens minted elsewhere with the same key.
    assertThat(
            provider
                .validateAndGetClaims(provider.generateAccessToken(user(User.Role.USER)))
                .getNotBefore())
        .isNull();
    assertThat(
            provider
                .validateAndGetClaims(provider.generateRefreshToken(user(User.Role.USER)))
                .getNotBefore())
        .isNull();
  }

  // ---------------------------------------------------------------- signature / algorithm

  @Test
  void tokenSignedWithADifferentSecret_isRejected() {
    SecretKey otherKey = Keys.hmacShaKeyFor(OTHER_SECRET.getBytes(StandardCharsets.UTF_8));
    String token =
        Jwts.builder()
            .subject(UUID.randomUUID().toString())
            .claim("type", "access")
            .claim("roles", List.of("ADMIN"))
            .issuedAt(Date.from(Instant.now()))
            .expiration(Date.from(Instant.now().plusSeconds(3600)))
            .signWith(otherKey)
            .compact();

    assertThat(provider.isTokenValid(token)).isFalse();
    assertThatThrownBy(() -> provider.validateAndGetClaims(token)).isInstanceOf(JwtException.class);
  }

  @Test
  void unsignedTokenWithAlgNone_isRejected() {
    String token =
        Jwts.builder()
            .subject(UUID.randomUUID().toString())
            .claim("type", "access")
            .claim("roles", List.of("ADMIN"))
            .expiration(Date.from(Instant.now().plusSeconds(3600)))
            .compact();

    assertThat(provider.isTokenValid(token)).isFalse();
  }

  @Test
  void tamperedPayload_isRejected() {
    String token = provider.generateAccessToken(user(User.Role.USER));
    String[] parts = token.split("\\.");
    String tampered =
        parts[0] + "." + parts[1].substring(0, parts[1].length() - 2) + "AA." + parts[2];

    assertThat(provider.isTokenValid(tampered)).isFalse();
  }

  @Test
  void structurallyInvalidTokens_areRejected() {
    assertThat(provider.isTokenValid("")).isFalse();
    assertThat(provider.isTokenValid("....")).isFalse();
    assertThat(provider.isTokenValid("Bearer eyJ")).isFalse();
  }

  // ---------------------------------------------------------------- type confusion

  @Test
  void refreshToken_cannotBeUsedAsAnAccessToken() {
    String refreshToken = provider.generateRefreshToken(user(User.Role.USER));

    assertThatThrownBy(() -> provider.validateTokenAndGetUserId(refreshToken))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Refresh token cannot be used as access token");
  }

  @Test
  void accessToken_cannotBeUsedAsARefreshToken() {
    String accessToken = provider.generateAccessToken(user(User.Role.USER));

    assertThatThrownBy(() -> provider.validateRefreshTokenAndGetUserId(accessToken))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Token is not a refresh token");
  }

  @Test
  void tokenWithoutATypeClaim_isAcceptedAsAnAccessToken() {
    // FINDING (pinned, not fixed): validateTokenAndGetUserId only rejects type == "refresh".
    // A token carrying no type claim at all is treated as an access token.
    String userId = UUID.randomUUID().toString();
    String token = signed(claims -> claims.subject(userId), 3600, null);

    assertThat(provider.validateTokenAndGetUserId(token)).isEqualTo(userId);
  }

  @Test
  void tokenWithoutATypeClaim_isRejectedAsARefreshToken() {
    String token = signed(claims -> claims.subject(UUID.randomUUID().toString()), 3600, null);

    assertThatThrownBy(() -> provider.validateRefreshTokenAndGetUserId(token))
        .isInstanceOf(IllegalArgumentException.class);
  }

  // ---------------------------------------------------------------- claims

  @Test
  void tokenWithoutASubject_yieldsANullUserIdInsteadOfAnError() {
    // FINDING (pinned, not fixed): a signed token with no `sub` passes validation and returns
    // null. AuthController then calls UUID.fromString(null) -> 500. See
    // AdminRouteAuthorizationTest#tokenWithoutSubject_currentlyCausesAServerError.
    String token = signed(claims -> claims.claim("type", "access"), 3600, null);

    assertThat(provider.validateTokenAndGetUserId(token)).isNull();
  }

  @Test
  void accessTokenCarriesNoJti_soItCannotBeRevokedById() {
    // FINDING (pinned, not fixed): only refresh tokens get a jti, so there is no server-side
    // handle on an issued access token. This is why logout cannot revoke one.
    String accessToken = provider.generateAccessToken(user(User.Role.USER));

    assertThat(provider.extractJti(accessToken)).isNull();
  }

  @Test
  void refreshTokenJtiIsUniquePerIssue() {
    User user = user(User.Role.USER);

    String first = provider.extractJti(provider.generateRefreshToken(user));
    String second = provider.extractJti(provider.generateRefreshToken(user));

    assertThat(first).isNotBlank().isNotEqualTo(second);
  }

  @Test
  void refreshTokenCarriesNoRolesClaim() {
    String refreshToken = provider.generateRefreshToken(user(User.Role.ADMIN, User.Role.USER));
    Claims claims = provider.validateAndGetClaims(refreshToken);

    assertThat(claims.get("roles")).isNull();
  }

  @Test
  void accessTokenCarriesEveryRoleTheUserHolds() {
    String token = provider.generateAccessToken(user(User.Role.ADMIN, User.Role.USER));

    @SuppressWarnings("unchecked")
    List<String> roles = provider.validateAndGetClaims(token).get("roles", List.class);
    assertThat(roles).containsExactlyInAnyOrder("ADMIN", "USER");
  }

  @Test
  void accessTokenForAUserWithNoRoles_carriesAnEmptyRolesClaim() {
    User user = user();

    String token = provider.generateAccessToken(user);

    @SuppressWarnings("unchecked")
    List<String> roles = provider.validateAndGetClaims(token).get("roles", List.class);
    assertThat(roles).isEmpty();
  }

  @Test
  void rolesClaimIsNotBoundToTheUserRecord() {
    // FINDING (pinned, not fixed): authorization is claim-only. A token minted with this
    // service's key can assert any role; nothing re-reads the user's roles at request time,
    // so a role revoked in the database stays effective until the token expires.
    String userId = UUID.randomUUID().toString();
    String token =
        signed(
            claims ->
                claims.subject(userId).claim("type", "access").claim("roles", List.of("ADMIN")),
            3600,
            null);

    @SuppressWarnings("unchecked")
    List<String> roles = provider.validateAndGetClaims(token).get("roles", List.class);
    assertThat(roles).containsExactly("ADMIN");
  }

  @Test
  void expiryGettersReflectTheInjectedConfiguration() {
    JwtTokenProvider shortLived =
        new JwtTokenProvider(
            SECRET, 1,
            2); // nosemgrep: java.lang.security.audit.crypto.no-static-initialization-vector

    assertThat(shortLived.getAccessTokenExpiry()).isEqualTo(1);
    assertThat(shortLived.getRefreshTokenExpiry()).isEqualTo(2);
  }

  // ---------------------------------------------------------------- helpers

  /** Parses {@code token} exactly as {@code JwtTokenProvider} does, but at a frozen instant. */
  private Jws<Claims> parseAt(String token, Instant now) {
    return Jwts.parser()
        .verifyWith(key)
        .clock(() -> Date.from(now))
        .build()
        .parseSignedClaims(token);
  }

  private Instant expiryOf(String token) {
    return provider.validateAndGetClaims(token).getExpiration().toInstant();
  }

  private String notBeforeToken(Instant notBefore) {
    return Jwts.builder()
        .subject(UUID.randomUUID().toString())
        .claim("type", "access")
        .issuedAt(Date.from(notBefore.minus(1, ChronoUnit.HOURS)))
        .notBefore(Date.from(notBefore))
        .expiration(Date.from(notBefore.plus(1, ChronoUnit.HOURS)))
        .signWith(key)
        .compact();
  }

  private String signed(
      UnaryOperator<JwtBuilder> customizer, long expirySeconds, Instant notBefore) {
    Instant now = Instant.now();
    JwtBuilder builder =
        customizer
            .apply(Jwts.builder())
            .issuedAt(Date.from(now))
            .expiration(Date.from(now.plusSeconds(expirySeconds)));
    if (notBefore != null) {
      builder = builder.notBefore(Date.from(notBefore));
    }
    return builder.signWith(key).compact();
  }

  private static User user(User.Role... roles) {
    User user = new User();
    user.setId(UUID.randomUUID());
    user.setEmail("lifecycle@otterworks.dev");
    user.setDisplayName("Lifecycle User");
    user.setPasswordHash(
        "$2a$12$hashedpassword"); // nosemgrep: generic.secrets.security.detected-bcrypt-hash
    user.setRoles(roles.length == 0 ? Set.of() : Set.of(roles));
    return user;
  }
}
