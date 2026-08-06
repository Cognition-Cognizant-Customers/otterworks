package com.otterworks.auth.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/** End-to-end token handling: refresh rotation/replay, type confusion and forged tokens. */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@TestMethodOrder(MethodOrderer.Random.class)
class AuthTokenFlowTest {

  private static final String FOREIGN_SECRET =
      "a-completely-different-secret-that-is-also-at-least-32-bytes-long";

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;

  @BeforeEach
  void setUp() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  @Test
  void refresh_replayedRefreshToken_returns400() throws Exception {
    JsonNode tokens = register("replay@otterworks.dev");
    String refreshToken = tokens.get("refreshToken").asText();

    mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + refreshToken))
        .andExpect(status().isOk());

    mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + refreshToken))
        .andExpect(status().isBadRequest());
  }

  @Test
  void refresh_rotatedRefreshTokenFromPreviousRefresh_returns200() throws Exception {
    JsonNode tokens = register("rotation@otterworks.dev");

    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/auth/refresh")
                    .header("Authorization", "Bearer " + tokens.get("refreshToken").asText()))
            .andExpect(status().isOk())
            .andReturn();
    String rotated =
        objectMapper
            .readTree(result.getResponse().getContentAsString())
            .get("refreshToken")
            .asText();

    mockMvc
        .perform(post("/api/v1/auth/refresh").header("Authorization", "Bearer " + rotated))
        .andExpect(status().isOk());
  }

  @Test
  void refresh_accessTokenPresentedAsRefreshToken_returns400() throws Exception {
    JsonNode tokens = register("typeconfusion@otterworks.dev");

    mockMvc
        .perform(
            post("/api/v1/auth/refresh")
                .header("Authorization", "Bearer " + tokens.get("accessToken").asText()))
        .andExpect(status().isBadRequest());
  }

  @Test
  void profile_refreshTokenPresentedAsAccessToken_returns403() throws Exception {
    JsonNode tokens = register("refreshasaccess@otterworks.dev");

    mockMvc
        .perform(
            get("/api/v1/auth/profile")
                .header("Authorization", "Bearer " + tokens.get("refreshToken").asText()))
        .andExpect(status().isForbidden());
  }

  @Test
  void profile_tokenSignedWithDifferentSecret_returns403() throws Exception {
    JsonNode tokens = register("foreignsecret@otterworks.dev");
    String userId = tokens.get("user").get("id").asText();

    Instant now = Instant.now();
    String forged =
        Jwts.builder()
            .subject(userId)
            .claim("email", "foreignsecret@otterworks.dev")
            .claim("roles", java.util.List.of("USER"))
            .claim("type", "access")
            .issuedAt(Date.from(now))
            .expiration(Date.from(now.plus(1, ChronoUnit.HOURS)))
            .signWith(Keys.hmacShaKeyFor(FOREIGN_SECRET.getBytes(StandardCharsets.UTF_8)))
            .compact();

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + forged))
        .andExpect(status().isForbidden());
  }

  @Test
  void profile_unsignedAlgNoneToken_returns403() throws Exception {
    String userId = register("algnone@otterworks.dev").get("user").get("id").asText();
    Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
    String header =
        encoder.encodeToString(
            "{\"alg\":\"none\",\"typ\":\"JWT\"}".getBytes(StandardCharsets.UTF_8));
    String payload =
        encoder.encodeToString(
            String.format(
                    "{\"sub\":\"%s\",\"type\":\"access\",\"roles\":[\"ADMIN\"],\"exp\":%d}",
                    userId, Instant.now().plus(1, ChronoUnit.HOURS).getEpochSecond())
                .getBytes(StandardCharsets.UTF_8));

    mockMvc
        .perform(
            get("/api/v1/auth/profile")
                .header("Authorization", "Bearer " + header + "." + payload + "."))
        .andExpect(status().isForbidden());
  }

  @Test
  void profile_truncatedToken_returns403() throws Exception {
    String accessToken = register("truncated@otterworks.dev").get("accessToken").asText();
    String truncated = accessToken.substring(0, accessToken.length() - 5);

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + truncated))
        .andExpect(status().isForbidden());
  }

  @Test
  void profile_tokenForDeletedUser_returns400() throws Exception {
    JsonNode tokens = register("deleted@otterworks.dev");
    refreshTokenRepository.deleteAll();
    userRepository.deleteById(UUID.fromString(tokens.get("user").get("id").asText()));

    mockMvc
        .perform(
            get("/api/v1/auth/profile")
                .header("Authorization", "Bearer " + tokens.get("accessToken").asText()))
        .andExpect(status().isBadRequest());
  }

  private JsonNode register(String email) throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("email", email);
    body.put("password", "password123");
    body.put("displayName", "Token Flow User");

    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/auth/register")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(objectMapper.writeValueAsString(body)))
            .andExpect(status().isCreated())
            .andReturn();

    return objectMapper.readTree(result.getResponse().getContentAsString());
  }
}
