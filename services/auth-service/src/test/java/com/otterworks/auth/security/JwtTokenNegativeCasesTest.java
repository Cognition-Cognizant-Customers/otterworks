package com.otterworks.auth.security;

import static org.assertj.core.api.Assertions.*;

import com.otterworks.auth.entity.User;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import io.jsonwebtoken.security.SignatureException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.Date;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;

/** Forged, malformed and mis-typed tokens must never be accepted by {@link JwtTokenProvider}. */
@TestMethodOrder(MethodOrderer.Random.class)
class JwtTokenNegativeCasesTest {

  private static final String SECRET =
      "test-jwt-secret-otterworks-must-be-at-least-32-bytes-long-for-hmac";
  private static final String FOREIGN_SECRET =
      "a-completely-different-secret-that-is-also-at-least-32-bytes-long";

  private final JwtTokenProvider provider =
      new JwtTokenProvider(
          SECRET, 3600,
          2592000); // nosemgrep: java.lang.security.audit.crypto.no-static-initialization-vector

  @Test
  void isTokenValid_tokenSignedWithDifferentSecret_returnsFalse() {
    assertThat(provider.isTokenValid(tokenSignedWith(FOREIGN_SECRET))).isFalse();
  }

  @Test
  void validateAndGetClaims_tokenSignedWithDifferentSecret_throwsSignatureException() {
    String foreign = tokenSignedWith(FOREIGN_SECRET);

    assertThatThrownBy(() -> provider.validateAndGetClaims(foreign))
        .isInstanceOf(SignatureException.class);
  }

  @Test
  void isTokenValid_unsignedAlgNoneToken_returnsFalse() {
    assertThat(provider.isTokenValid(algNoneToken())).isFalse();
  }

  @Test
  void validateAndGetClaims_unsignedAlgNoneToken_throwsJwtException() {
    String token = algNoneToken();

    assertThatThrownBy(() -> provider.validateAndGetClaims(token)).isInstanceOf(JwtException.class);
  }

  @Test
  void isTokenValid_truncatedToken_returnsFalse() {
    String token = provider.generateAccessToken(user());

    assertThat(provider.isTokenValid(token.substring(0, token.length() - 5))).isFalse();
  }

  @Test
  void isTokenValid_tokenWithoutSignatureSegment_returnsFalse() {
    String token = provider.generateAccessToken(user());
    String unsigned = token.substring(0, token.lastIndexOf('.') + 1);

    assertThat(provider.isTokenValid(unsigned)).isFalse();
  }

  @Test
  void isTokenValid_tokenWithTamperedPayload_returnsFalse() {
    String token = provider.generateAccessToken(user());
    String[] parts = token.split("\\.");
    String tamperedPayload =
        Base64.getUrlEncoder()
            .withoutPadding()
            .encodeToString(
                new String(Base64.getUrlDecoder().decode(parts[1]), StandardCharsets.UTF_8)
                    .replace("\"USER\"", "\"ADMIN\"")
                    .getBytes(StandardCharsets.UTF_8));

    assertThat(provider.isTokenValid(parts[0] + "." + tamperedPayload + "." + parts[2])).isFalse();
  }

  @Test
  void isTokenValid_emptyToken_returnsFalse() {
    assertThat(provider.isTokenValid("")).isFalse();
  }

  @Test
  void isTokenValid_nonJwtGarbage_returnsFalse() {
    assertThat(provider.isTokenValid("not-a-jwt-at-all")).isFalse();
  }

  @Test
  void validateTokenAndGetUserId_refreshTokenPresented_throwsIllegalArgumentException() {
    String refreshToken = provider.generateRefreshToken(user());

    assertThatThrownBy(() -> provider.validateTokenAndGetUserId(refreshToken))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void validateRefreshTokenAndGetUserId_accessTokenPresented_throwsIllegalArgumentException() {
    String accessToken = provider.generateAccessToken(user());

    assertThatThrownBy(() -> provider.validateRefreshTokenAndGetUserId(accessToken))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void validateRefreshTokenAndGetUserId_tokenWithoutTypeClaim_throwsIllegalArgumentException() {
    Instant now = Instant.now();
    String typeless =
        Jwts.builder()
            .subject(UUID.randomUUID().toString())
            .issuedAt(Date.from(now))
            .expiration(Date.from(now.plus(1, ChronoUnit.HOURS)))
            .signWith(Keys.hmacShaKeyFor(SECRET.getBytes(StandardCharsets.UTF_8)))
            .compact();

    assertThatThrownBy(() -> provider.validateRefreshTokenAndGetUserId(typeless))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void extractJti_accessTokenHasNoJti_returnsNull() {
    assertThat(provider.extractJti(provider.generateAccessToken(user()))).isNull();
  }

  private String tokenSignedWith(String secret) {
    Instant now = Instant.now();
    return Jwts.builder()
        .subject(UUID.randomUUID().toString())
        .claim("type", "access")
        .claim("roles", List.of("ADMIN"))
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plus(1, ChronoUnit.HOURS)))
        .signWith(Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8)))
        .compact();
  }

  private String algNoneToken() {
    Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
    String header =
        encoder.encodeToString(
            "{\"alg\":\"none\",\"typ\":\"JWT\"}".getBytes(StandardCharsets.UTF_8));
    String payload =
        encoder.encodeToString(
            String.format(
                    "{\"sub\":\"%s\",\"type\":\"access\",\"roles\":[\"ADMIN\"],\"exp\":%d}",
                    UUID.randomUUID(), Instant.now().plus(1, ChronoUnit.HOURS).getEpochSecond())
                .getBytes(StandardCharsets.UTF_8));
    return header + "." + payload + ".";
  }

  private static User user() {
    User user = new User();
    user.setId(UUID.randomUUID());
    user.setEmail("negative@otterworks.dev");
    user.setDisplayName("Negative User");
    user.setRoles(Set.of(User.Role.USER));
    return user;
  }
}
