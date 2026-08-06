package com.otterworks.auth.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.auth.entity.RefreshToken;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import com.otterworks.auth.security.JwtTokenProvider;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.ResultActions;

/**
 * End-to-end refresh-token rotation/replay and logout semantics over the HTTP surface.
 *
 * <p>Every test registers its own uniquely-named account, so nothing here depends on execution
 * order or on state left behind by another test.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AuthTokenLifecycleIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;
  @Autowired private JwtTokenProvider jwtTokenProvider;

  @BeforeEach
  void resetDatabase() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  // ---------------------------------------------------------------- rotation & replay

  @Test
  void refresh_rotatesTheRefreshTokenAndRevokesThePredecessor() throws Exception {
    JsonNode session = register("rotate@otterworks.dev");
    String original = session.get("refreshToken").asText();
    String originalJti = jwtTokenProvider.extractJti(original);

    JsonNode rotated =
        objectMapper.readTree(refresh(original).andReturn().getResponse().getContentAsString());
    String rotatedJti = jwtTokenProvider.extractJti(rotated.get("refreshToken").asText());

    assertThat(rotatedJti).isNotEqualTo(originalJti);
    assertThat(revokedFlagOf(originalJti)).isTrue();
    assertThat(revokedFlagOf(rotatedJti)).isFalse();
  }

  @Test
  void refresh_replayingTheSameRefreshTokenIsRejected() throws Exception {
    JsonNode session = register("replay@otterworks.dev");
    String original = session.get("refreshToken").asText();

    refresh(original).andExpect(status().isOk());

    mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + original))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.message").value("Invalid or revoked refresh token"));
  }

  @Test
  void refresh_replayIsRejectedForEveryPredecessorInARotationChain() throws Exception {
    String first = register("chain@otterworks.dev").get("refreshToken").asText();
    String second =
        objectMapper
            .readTree(refresh(first).andReturn().getResponse().getContentAsString())
            .get("refreshToken")
            .asText();
    String third =
        objectMapper
            .readTree(refresh(second).andReturn().getResponse().getContentAsString())
            .get("refreshToken")
            .asText();

    for (String burned : List.of(first, second)) {
      mockMvc
          .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + burned))
          .andExpect(status().isBadRequest());
    }
    refresh(third).andExpect(status().isOk());
  }

  @Test
  void refresh_rotationDoesNotRevokeOtherSessionsOfTheSameUser() throws Exception {
    // FINDING (pinned, not fixed): replaying a burned refresh token is rejected, but it does
    // not trigger the usual "reuse detected -> revoke the whole token family" response, so a
    // stolen-and-replayed token leaves the thief's rotated session intact.
    String sessionA = register("twosessions@otterworks.dev").get("refreshToken").asText();
    String sessionB = login("twosessions@otterworks.dev").get("refreshToken").asText();

    refresh(sessionA).andExpect(status().isOk());
    mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + sessionA))
        .andExpect(status().isBadRequest());

    refresh(sessionB).andExpect(status().isOk());
  }

  @Test
  void refresh_withAnAccessTokenIsRejected() throws Exception {
    String accessToken = register("wrongtype@otterworks.dev").get("accessToken").asText();

    mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + accessToken))
        .andExpect(status().isBadRequest());
  }

  @Test
  void refresh_withAMalformedTokenIsRejectedAsUnauthorized() throws Exception {
    mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer not.a.jwt"))
        .andExpect(status().isUnauthorized());
  }

  @Test
  void refresh_withoutAnAuthorizationHeaderCurrentlyReturns500() throws Exception {
    // FINDING (pinned, not fixed): a missing Authorization header raises
    // MissingRequestHeaderException, which GlobalExceptionHandler only catches through its
    // catch-all Exception handler, so a plain client error is reported as a server error.
    mockMvc.perform(post("/api/v1/auth/refresh")).andExpect(status().isInternalServerError());
  }

  @Test
  void refresh_withAnExpiredStoredRowIsRejected() throws Exception {
    String refreshToken = register("expiredrow@otterworks.dev").get("refreshToken").asText();
    String jti = jwtTokenProvider.extractJti(refreshToken);
    expireStoredToken(jti, Instant.now().minusSeconds(1));

    mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + refreshToken))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.message").value("Refresh token expired"));
  }

  @Test
  void refresh_issuesExactlyOneNewRowPerRotation() throws Exception {
    String refreshToken = register("rowcount@otterworks.dev").get("refreshToken").asText();

    refresh(refreshToken).andExpect(status().isOk());

    assertThat(refreshTokenRepository.count()).isEqualTo(2);
    assertThat(refreshTokenRepository.findAll().stream().filter(RefreshToken::isRevoked).count())
        .isEqualTo(1);
  }

  // ---------------------------------------------------------------- logout

  @Test
  void logout_revokesEveryRefreshTokenSoRefreshStopsWorking() throws Exception {
    JsonNode session = register("logout@otterworks.dev");

    mockMvc
        .perform(
            post("/api/v1/auth/logout")
                .header("Authorization", "Bearer " + session.get("accessToken").asText()))
        .andExpect(status().isNoContent());

    mockMvc
        .perform(
            post("/api/v1/auth/refresh")
                .header("Authorization", "Bearer " + session.get("refreshToken").asText()))
        .andExpect(status().isBadRequest());
  }

  @Test
  void logout_revokesRefreshTokensIssuedByEveryOtherSession() throws Exception {
    JsonNode sessionA = register("logoutall@otterworks.dev");
    JsonNode sessionB = login("logoutall@otterworks.dev");

    mockMvc
        .perform(
            post("/api/v1/auth/logout")
                .header("Authorization", "Bearer " + sessionA.get("accessToken").asText()))
        .andExpect(status().isNoContent());

    mockMvc
        .perform(
            post("/api/v1/auth/refresh")
                .header("Authorization", "Bearer " + sessionB.get("refreshToken").asText()))
        .andExpect(status().isBadRequest());
  }

  @Test
  void logout_doesNotInvalidateAnInFlightAccessToken() throws Exception {
    // FINDING (pinned, not fixed): logout only revokes refresh tokens. The access token is a
    // stateless JWT with no jti and no deny list, so it keeps authenticating protected routes
    // for the remainder of its 1h TTL after the user has logged out.
    JsonNode session = register("inflight@otterworks.dev");
    String accessToken = session.get("accessToken").asText();

    mockMvc
        .perform(post("/api/v1/auth/logout").header("Authorization", "Bearer " + accessToken))
        .andExpect(status().isNoContent());

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + accessToken))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.email").value("inflight@otterworks.dev"));
  }

  @Test
  @Disabled("FINDING: logout is refresh-token-only; enable when access tokens can be revoked")
  void logout_shouldInvalidateAnInFlightAccessToken() throws Exception {
    JsonNode session = register("inflight-desired@otterworks.dev");
    String accessToken = session.get("accessToken").asText();

    mockMvc
        .perform(post("/api/v1/auth/logout").header("Authorization", "Bearer " + accessToken))
        .andExpect(status().isNoContent());

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + accessToken))
        .andExpect(status().isForbidden());
  }

  @Test
  void logout_isIdempotent() throws Exception {
    String accessToken = register("idempotent@otterworks.dev").get("accessToken").asText();

    for (int i = 0; i < 3; i++) {
      mockMvc
          .perform(post("/api/v1/auth/logout").header("Authorization", "Bearer " + accessToken))
          .andExpect(status().isNoContent());
    }

    assertThat(refreshTokenRepository.findAll()).allMatch(RefreshToken::isRevoked);
  }

  @Test
  void logout_requiresAuthentication() throws Exception {
    mockMvc.perform(post("/api/v1/auth/logout")).andExpect(status().isForbidden());
  }

  @Test
  void logout_withARefreshTokenAsBearerIsRejected() throws Exception {
    // The JWT filter deliberately skips refresh tokens, so the request arrives unauthenticated.
    String refreshToken = register("logoutrefresh@otterworks.dev").get("refreshToken").asText();

    mockMvc
        .perform(post("/api/v1/auth/logout").header("Authorization", "Bearer " + refreshToken))
        .andExpect(status().isForbidden());
  }

  @Test
  void logout_thenLoginIssuesAUsableRefreshToken() throws Exception {
    JsonNode session = register("relogin@otterworks.dev");
    mockMvc
        .perform(
            post("/api/v1/auth/logout")
                .header("Authorization", "Bearer " + session.get("accessToken").asText()))
        .andExpect(status().isNoContent());

    String freshRefreshToken = login("relogin@otterworks.dev").get("refreshToken").asText();

    refresh(freshRefreshToken).andExpect(status().isOk());
  }

  // ---------------------------------------------------------------- password change

  @Test
  void changePassword_revokesEveryRefreshToken() throws Exception {
    JsonNode session = register("pwrevoke@otterworks.dev");

    mockMvc
        .perform(
            post("/api/v1/auth/change-password")
                .header("Authorization", "Bearer " + session.get("accessToken").asText())
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    "{\"currentPassword\": \"password123\", \"newPassword\": \"newpassword456\"}"))
        .andExpect(status().isNoContent());

    mockMvc
        .perform(
            post("/api/v1/auth/refresh")
                .header("Authorization", "Bearer " + session.get("refreshToken").asText()))
        .andExpect(status().isBadRequest());
  }

  @Test
  void changePassword_doesNotInvalidateAnInFlightAccessToken() throws Exception {
    // FINDING (pinned, not fixed): same root cause as logout - after a password change the
    // previously issued access token still authenticates.
    JsonNode session = register("pwinflight@otterworks.dev");
    String accessToken = session.get("accessToken").asText();

    mockMvc
        .perform(
            post("/api/v1/auth/change-password")
                .header("Authorization", "Bearer " + accessToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    "{\"currentPassword\": \"password123\", \"newPassword\": \"newpassword456\"}"))
        .andExpect(status().isNoContent());

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + accessToken))
        .andExpect(status().isOk());
  }

  @Test
  void changePassword_withTheWrongCurrentPasswordLeavesRefreshTokensAlone() throws Exception {
    JsonNode session = register("pwwrong@otterworks.dev");

    mockMvc
        .perform(
            post("/api/v1/auth/change-password")
                .header("Authorization", "Bearer " + session.get("accessToken").asText())
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    "{\"currentPassword\": \"not-my-password\", \"newPassword\": \"newpassword456\"}"))
        .andExpect(status().isBadRequest());

    refresh(session.get("refreshToken").asText()).andExpect(status().isOk());
  }

  @Test
  void changePassword_requiresAuthentication() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/auth/change-password")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    "{\"currentPassword\": \"password123\", \"newPassword\": \"newpassword456\"}"))
        .andExpect(status().isForbidden());
  }

  // ---------------------------------------------------------------- helpers

  private JsonNode register(String email) throws Exception {
    String body =
        String.format(
            "{\"email\": \"%s\", \"password\": \"password123\", \"displayName\": \"Lifecycle\"}",
            email);
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/auth/register").contentType(MediaType.APPLICATION_JSON).content(body))
            .andExpect(status().isCreated())
            .andReturn();
    return objectMapper.readTree(result.getResponse().getContentAsString());
  }

  private JsonNode login(String email) throws Exception {
    String body = String.format("{\"email\": \"%s\", \"password\": \"password123\"}", email);
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/auth/login").contentType(MediaType.APPLICATION_JSON).content(body))
            .andExpect(status().isOk())
            .andReturn();
    return objectMapper.readTree(result.getResponse().getContentAsString());
  }

  private ResultActions refresh(String refreshToken) throws Exception {
    return mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + refreshToken))
        .andExpect(status().isOk());
  }

  private boolean revokedFlagOf(String jti) {
    return refreshTokenRepository.findAll().stream()
        .filter(t -> t.getTokenId().equals(jti))
        .findFirst()
        .orElseThrow(() -> new AssertionError("no refresh token row for jti " + jti))
        .isRevoked();
  }

  private void expireStoredToken(String jti, Instant expiresAt) {
    RefreshToken row =
        refreshTokenRepository.findAll().stream()
            .filter(t -> t.getTokenId().equals(jti))
            .findFirst()
            .orElseThrow(() -> new AssertionError("no refresh token row for jti " + jti));
    row.setExpiresAt(expiresAt);
    refreshTokenRepository.save(row);
  }
}
