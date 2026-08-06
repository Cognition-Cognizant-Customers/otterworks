package com.otterworks.auth.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import java.util.LinkedHashMap;
import java.util.Map;
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
import org.springframework.test.web.servlet.ResultActions;

/**
 * Boundary coverage for the bean-validation constraints declared on the auth DTOs: password length
 * ({@code @Size(min = 8, max = 128)}), display name length ({@code @Size(min = 1, max = 100)}) and
 * avatar URL length ({@code @Size(max = 500)}).
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@TestMethodOrder(MethodOrderer.Random.class)
class AuthValidationBoundaryTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;

  @BeforeEach
  void setUp() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  // --- RegisterRequest.password: @Size(min = 8, max = 128) ---

  @Test
  void register_passwordOf7Chars_returns400() throws Exception {
    register("pw7@otterworks.dev", "a".repeat(7), "Boundary User")
        .andExpect(status().isBadRequest());
  }

  @Test
  void register_passwordOf8Chars_returns201() throws Exception {
    register("pw8@otterworks.dev", "a".repeat(8), "Boundary User").andExpect(status().isCreated());
  }

  @Test
  void register_passwordOf128Chars_returns201() throws Exception {
    register("pw128@otterworks.dev", "a".repeat(128), "Boundary User")
        .andExpect(status().isCreated());
  }

  @Test
  void register_passwordOf129Chars_returns400() throws Exception {
    register("pw129@otterworks.dev", "a".repeat(129), "Boundary User")
        .andExpect(status().isBadRequest());
  }

  // --- ChangePasswordRequest.newPassword: @Size(min = 8, max = 128) ---

  @Test
  void changePassword_newPasswordOf7Chars_returns400() throws Exception {
    changePassword("cpw7@otterworks.dev", "b".repeat(7)).andExpect(status().isBadRequest());
  }

  @Test
  void changePassword_newPasswordOf8Chars_returns204() throws Exception {
    changePassword("cpw8@otterworks.dev", "b".repeat(8)).andExpect(status().isNoContent());
  }

  @Test
  void changePassword_newPasswordOf128Chars_returns204() throws Exception {
    changePassword("cpw128@otterworks.dev", "b".repeat(128)).andExpect(status().isNoContent());
  }

  @Test
  void changePassword_newPasswordOf129Chars_returns400() throws Exception {
    changePassword("cpw129@otterworks.dev", "b".repeat(129)).andExpect(status().isBadRequest());
  }

  // --- RegisterRequest.displayName: @Size(min = 1, max = 100) ---

  @Test
  void register_displayNameOf0Chars_returns400() throws Exception {
    register("dn0@otterworks.dev", "password123", "").andExpect(status().isBadRequest());
  }

  @Test
  void register_displayNameOf1Char_returns201() throws Exception {
    register("dn1@otterworks.dev", "password123", "x").andExpect(status().isCreated());
  }

  @Test
  void register_displayNameOf100Chars_returns201() throws Exception {
    register("dn100@otterworks.dev", "password123", "x".repeat(100))
        .andExpect(status().isCreated());
  }

  @Test
  void register_displayNameOf101Chars_returns400() throws Exception {
    register("dn101@otterworks.dev", "password123", "x".repeat(101))
        .andExpect(status().isBadRequest());
  }

  /**
   * 100 non-ASCII BMP code points are each a single UTF-16 unit, so the constraint is satisfied.
   */
  @Test
  void register_displayNameOf100NonAsciiBmpCodePoints_returns201() throws Exception {
    String name = "漢".repeat(100);
    register("dnbmp@otterworks.dev", "password123", name).andExpect(status().isCreated());
  }

  /**
   * Pins the observed counting unit of {@code @Size}: 100 supplementary-plane code points are 200
   * UTF-16 units, and the request is rejected. {@code @Size} therefore counts UTF-16 units ({@code
   * String.length()}), not code points.
   */
  @Test
  void register_displayNameOf100NonAsciiSupplementaryCodePoints_returns400() throws Exception {
    String name = "\uD83D\uDE00".repeat(100);
    register("dnemoji@otterworks.dev", "password123", name).andExpect(status().isBadRequest());
  }

  // --- UpdateProfileRequest.displayName: @Size(min = 1, max = 100) ---

  @Test
  void updateProfile_displayNameOf0Chars_returns400() throws Exception {
    updateProfile("updn0@otterworks.dev", Map.of("displayName", ""))
        .andExpect(status().isBadRequest());
  }

  @Test
  void updateProfile_displayNameOf1Char_returns200() throws Exception {
    updateProfile("updn1@otterworks.dev", Map.of("displayName", "y"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.displayName").value("y"));
  }

  @Test
  void updateProfile_displayNameOf100Chars_returns200() throws Exception {
    updateProfile("updn100@otterworks.dev", Map.of("displayName", "y".repeat(100)))
        .andExpect(status().isOk());
  }

  @Test
  void updateProfile_displayNameOf101Chars_returns400() throws Exception {
    updateProfile("updn101@otterworks.dev", Map.of("displayName", "y".repeat(101)))
        .andExpect(status().isBadRequest());
  }

  // --- UpdateProfileRequest.avatarUrl: @Size(max = 500) ---

  @Test
  void updateProfile_avatarUrlOf499Chars_returns200() throws Exception {
    updateProfile("av499@otterworks.dev", Map.of("avatarUrl", avatarUrlOfLength(499)))
        .andExpect(status().isOk());
  }

  @Test
  void updateProfile_avatarUrlOf500Chars_returns200() throws Exception {
    updateProfile("av500@otterworks.dev", Map.of("avatarUrl", avatarUrlOfLength(500)))
        .andExpect(status().isOk());
  }

  @Test
  void updateProfile_avatarUrlOf501Chars_returns400() throws Exception {
    updateProfile("av501@otterworks.dev", Map.of("avatarUrl", avatarUrlOfLength(501)))
        .andExpect(status().isBadRequest());
  }

  private String avatarUrlOfLength(int length) {
    String prefix = "https://cdn.otterworks.dev/";
    return prefix + "a".repeat(length - prefix.length());
  }

  private ResultActions register(String email, String password, String displayName)
      throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("email", email);
    body.put("password", password);
    body.put("displayName", displayName);
    return mockMvc.perform(
        post("/api/v1/auth/register")
            .contentType(MediaType.APPLICATION_JSON)
            .content(objectMapper.writeValueAsString(body)));
  }

  private ResultActions changePassword(String email, String newPassword) throws Exception {
    String accessToken = registerAndGetAccessToken(email);
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("currentPassword", "password123");
    body.put("newPassword", newPassword);
    return mockMvc.perform(
        post("/api/v1/auth/change-password")
            .header("Authorization", "Bearer " + accessToken)
            .contentType(MediaType.APPLICATION_JSON)
            .content(objectMapper.writeValueAsString(body)));
  }

  private ResultActions updateProfile(String email, Map<String, Object> body) throws Exception {
    String accessToken = registerAndGetAccessToken(email);
    return mockMvc.perform(
        put("/api/v1/auth/profile")
            .header("Authorization", "Bearer " + accessToken)
            .contentType(MediaType.APPLICATION_JSON)
            .content(objectMapper.writeValueAsString(body)));
  }

  private String registerAndGetAccessToken(String email) throws Exception {
    MvcResult result =
        register(email, "password123", "Boundary User").andExpect(status().isCreated()).andReturn();
    JsonNode json = objectMapper.readTree(result.getResponse().getContentAsString());
    return json.get("accessToken").asText();
  }
}
