package com.otterworks.auth.security;

import static org.assertj.core.api.Assertions.*;

import com.otterworks.auth.entity.User;
import com.otterworks.auth.testsupport.JwtFixtures;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.PrematureJwtException;
import io.jsonwebtoken.security.SignatureException;
import java.time.Instant;
import java.util.Date;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * {@code exp} / {@code nbf} edges for {@link JwtTokenProvider} (WP-05).
 *
 * <p>The provider builds its parser with {@code Jwts.parser().verifyWith(key)} and never sets an
 * allowed clock skew, so the effective rule is "reject once {@code exp} is strictly in the past,
 * with zero tolerance for a fast or slow client clock". These tests pin that rule from both sides.
 *
 * <p>The exactly-at-{@code exp} case is asserted through a parser driven by a <em>fixed</em> clock
 * rather than the wall clock: comparing an instant against {@code Instant.now()} at parse time
 * would be a race, and a racy test is worse than no test.
 */
class JwtClockSkewTest {

  private static final long ACCESS_TTL_SECONDS = 3600;
  private static final long REFRESH_TTL_SECONDS = 2592000;

  private JwtTokenProvider provider;

  @BeforeEach
  void setUp() {
    provider =
        new JwtTokenProvider(
            JwtFixtures.TEST_SECRET, ACCESS_TTL_SECONDS, REFRESH_TTL_SECONDS); // nosemgrep:
    // java.lang.security.audit.crypto.no-static-initialization-vector
  }

  // ---------- exp: one second either side ----------

  @Test
  void validate_shouldAcceptATokenWhoseExpiryIsStillAhead() {
    Instant now = Instant.now();
    String token = JwtFixtures.accessToken(UUID.randomUUID().toString(), now, now.plusSeconds(60));

    assertThat(provider.isTokenValid(token)).isTrue();
  }

  @Test
  void validate_shouldRejectATokenThatExpiredOneSecondAgo() {
    Instant now = Instant.now();
    String token =
        JwtFixtures.accessToken(
            UUID.randomUUID().toString(), now.minusSeconds(61), now.minusSeconds(1));

    assertThat(provider.isTokenValid(token)).isFalse();
    assertThatThrownBy(() -> provider.validateAndGetClaims(token))
        .isInstanceOf(ExpiredJwtException.class);
  }

  @Test
  void validate_shouldRejectATokenThatExpiredLongAgo() {
    Instant longAgo = Instant.parse("2020-01-01T00:00:00Z");
    String token =
        JwtFixtures.accessToken(UUID.randomUUID().toString(), longAgo, longAgo.plusSeconds(3600));

    assertThat(provider.isTokenValid(token)).isFalse();
  }

  /**
   * The exact boundary, measured against a fixed clock: at {@code now == exp} the token is still
   * accepted, at one millisecond past it is not. Together with the two cases above this is the
   * boundary trio for {@code exp}.
   */
  @Test
  void validate_shouldTreatExpAsInclusiveOfTheExpiryInstantItself() {
    Instant issued = Instant.parse("2030-06-01T12:00:00Z");
    Instant expiry = issued.plusSeconds(60);
    String token = JwtFixtures.accessToken(UUID.randomUUID().toString(), issued, expiry);

    assertThat(parsesAt(token, expiry.minusMillis(1))).as("exp - 1ms").isTrue();
    assertThat(parsesAt(token, expiry)).as("exp exactly").isTrue();
    assertThat(parsesAt(token, expiry.plusMillis(1))).as("exp + 1ms").isFalse();
  }

  @Test
  void validate_shouldNotTolerateAnyClockSkewPastExpiry() {
    Instant issued = Instant.parse("2030-06-01T12:00:00Z");
    Instant expiry = issued.plusSeconds(60);
    String token = JwtFixtures.accessToken(UUID.randomUUID().toString(), issued, expiry);

    assertThat(parsesAt(token, expiry.plusSeconds(1))).as("client clock 1s fast").isFalse();
    assertThat(parsesAt(token, expiry.plusSeconds(30))).as("client clock 30s fast").isFalse();
  }

  // ---------- nbf ----------

  @Test
  void validate_shouldRejectATokenThatIsNotYetValid() {
    Instant now = Instant.now();
    String token =
        JwtFixtures.accessTokenNotBefore(
            UUID.randomUUID().toString(), now.plusSeconds(60), now.plusSeconds(3600));

    assertThat(provider.isTokenValid(token)).isFalse();
    assertThatThrownBy(() -> provider.validateAndGetClaims(token))
        .isInstanceOf(PrematureJwtException.class);
  }

  @Test
  void validate_shouldAcceptATokenWhoseNotBeforeHasPassed() {
    Instant now = Instant.now();
    String token =
        JwtFixtures.accessTokenNotBefore(
            UUID.randomUUID().toString(), now.minusSeconds(60), now.plusSeconds(3600));

    assertThat(provider.isTokenValid(token)).isTrue();
  }

  @Test
  void validate_shouldTreatNbfAsInclusiveOfTheInstantItself() {
    Instant notBefore = Instant.parse("2030-06-01T12:00:00Z");
    String token =
        JwtFixtures.accessTokenNotBefore(
            UUID.randomUUID().toString(), notBefore, notBefore.plusSeconds(3600));

    assertThat(parsesAt(token, notBefore.minusMillis(1))).as("nbf - 1ms").isFalse();
    assertThat(parsesAt(token, notBefore)).as("nbf exactly").isTrue();
    assertThat(parsesAt(token, notBefore.plusMillis(1))).as("nbf + 1ms").isTrue();
  }

  /**
   * The provider never emits an {@code nbf} claim, so its own tokens are usable the instant they
   * are minted. Pinned so that introducing an activation delay is a visible change.
   */
  @Test
  void generatedTokens_shouldCarryNoNotBeforeClaim() {
    User user = user();

    Claims access = provider.validateAndGetClaims(provider.generateAccessToken(user));
    Claims refresh = provider.validateAndGetClaims(provider.generateRefreshToken(user));

    assertThat(access.getNotBefore()).isNull();
    assertThat(refresh.getNotBefore()).isNull();
  }

  // ---------- iat ----------

  /**
   * Observed behaviour, not a recommendation: {@code iat} is not validated, so a token minted by a
   * clock an hour ahead is accepted as long as {@code exp} has not passed.
   */
  @Test
  void validate_currentlyAcceptsATokenIssuedInTheFuture() {
    Instant now = Instant.now();
    String token =
        JwtFixtures.accessToken(
            UUID.randomUUID().toString(), now.plusSeconds(3600), now.plusSeconds(7200));

    assertThat(provider.isTokenValid(token)).isTrue();
  }

  // ---------- ttl configuration ----------

  @Test
  void generateAccessToken_shouldExpireExactlyTheConfiguredTtlAfterIssuance() {
    User user = user();

    Claims claims = provider.validateAndGetClaims(provider.generateAccessToken(user));

    assertThat(claims.getExpiration().toInstant())
        .isEqualTo(claims.getIssuedAt().toInstant().plusSeconds(ACCESS_TTL_SECONDS));
  }

  @Test
  void generateRefreshToken_shouldExpireExactlyTheConfiguredTtlAfterIssuance() {
    User user = user();

    Claims claims = provider.validateAndGetClaims(provider.generateRefreshToken(user));

    assertThat(claims.getExpiration().toInstant())
        .isEqualTo(claims.getIssuedAt().toInstant().plusSeconds(REFRESH_TTL_SECONDS));
  }

  @Test
  void aNegativeTtlShouldProduceATokenThatIsAlreadyExpired() {
    JwtTokenProvider expiredProvider =
        new JwtTokenProvider(
            JwtFixtures.TEST_SECRET,
            -1,
            -1); // nosemgrep: java.lang.security.audit.crypto.no-static-initialization-vector

    assertThat(expiredProvider.isTokenValid(expiredProvider.generateAccessToken(user()))).isFalse();
    assertThat(expiredProvider.isTokenValid(expiredProvider.generateRefreshToken(user())))
        .isFalse();
  }

  // ---------- signature and claim negatives ----------

  @Test
  void validate_shouldRejectATokenSignedWithAnotherKey() {
    String token =
        JwtFixtures.accessTokenSignedWithForeignKey(
            UUID.randomUUID().toString(), java.util.List.of("USER"));

    assertThat(provider.isTokenValid(token)).isFalse();
    assertThatThrownBy(() -> provider.validateAndGetClaims(token))
        .isInstanceOf(SignatureException.class);
  }

  @Test
  void validate_shouldRejectATokenWhosePayloadWasSwappedForAnothersUnderTheSameSignature() {
    String mine = provider.generateAccessToken(user());
    String theirs = provider.generateAccessToken(user());
    String[] mineParts = mine.split("\\.");
    String[] theirParts = theirs.split("\\.");
    String spliced = mineParts[0] + "." + theirParts[1] + "." + mineParts[2];

    assertThat(spliced).isNotEqualTo(mine).isNotEqualTo(theirs);
    assertThat(provider.isTokenValid(spliced)).isFalse();
  }

  @Test
  void validateRefreshToken_shouldRejectAnExpiredRefreshTokenBeforeCheckingItsType() {
    Instant now = Instant.now();
    String expiredAccess =
        JwtFixtures.accessToken(
            UUID.randomUUID().toString(), now.minusSeconds(120), now.minusSeconds(60));

    assertThatThrownBy(() -> provider.validateRefreshTokenAndGetUserId(expiredAccess))
        .isInstanceOf(ExpiredJwtException.class);
  }

  @Test
  void validateToken_shouldRejectARefreshTokenUsedAsAnAccessToken() {
    String refreshToken = provider.generateRefreshToken(user());

    assertThatThrownBy(() -> provider.validateTokenAndGetUserId(refreshToken))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Refresh token cannot be used as access token");
  }

  @Test
  void validateRefreshToken_shouldRejectATokenWithNoTypeClaim() {
    String token = JwtFixtures.tokenWithoutTypeClaim(UUID.randomUUID().toString());

    assertThatThrownBy(() -> provider.validateRefreshTokenAndGetUserId(token))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Token is not a refresh token");
  }

  @Test
  void validateToken_shouldReturnANullSubjectWhenTheTokenHasNoSub() {
    String token = JwtFixtures.tokenWithoutSubject();

    assertThat(provider.validateTokenAndGetUserId(token)).isNull();
  }

  @Test
  void extractJti_shouldReturnNullForATokenWithoutAJti() {
    String token = provider.generateAccessToken(user());

    assertThat(provider.extractJti(token)).isNull();
  }

  @Test
  void isTokenValid_shouldReturnFalseForEmptyAndNullInput() {
    assertThat(provider.isTokenValid("")).isFalse();
    assertThat(provider.isTokenValid(null)).isFalse();
    assertThat(provider.isTokenValid("   ")).isFalse();
  }

  // ---------- helpers ----------

  /** Parses {@code token} as the provider would, but at instant {@code at} instead of now. */
  private boolean parsesAt(String token, Instant at) {
    try {
      Jwts.parser()
          .verifyWith(JwtFixtures.testKey())
          .clock(() -> Date.from(at))
          .build()
          .parseSignedClaims(token);
      return true;
    } catch (RuntimeException e) {
      return false;
    }
  }

  private User user() {
    User user = new User();
    user.setId(UUID.randomUUID());
    user.setEmail("skew@otterworks.dev");
    user.setDisplayName("Skew User");
    user.setPasswordHash("$2a$12$hash"); // nosemgrep: generic.secrets.security.detected-bcrypt-hash
    user.setRoles(Set.of(User.Role.USER));
    return user;
  }
}
