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
import org.junit.jupiter.api.Disabled;
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
 * Registration, login and change-password negative cases: missing, empty and whitespace-only
 * fields, case-only email collisions, and wrong credentials.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@TestMethodOrder(MethodOrderer.Random.class)
class AuthNegativeCasesTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;

  @BeforeEach
  void setUp() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  // --- Registration: empty vs absent vs whitespace-only ---

  @Test
  void register_emptyEmail_returns400() throws Exception {
    postRegister(fields("", "password123", "Empty Email")).andExpect(status().isBadRequest());
  }

  @Test
  void register_absentEmail_returns400() throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("password", "password123");
    body.put("displayName", "Absent Email");
    postRegister(body).andExpect(status().isBadRequest());
  }

  @Test
  void register_whitespaceOnlyEmail_returns400() throws Exception {
    postRegister(fields("   ", "password123", "Blank Email")).andExpect(status().isBadRequest());
  }

  @Test
  void register_emptyPassword_returns400() throws Exception {
    postRegister(fields("emptypw@otterworks.dev", "", "Empty Password"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void register_absentPassword_returns400() throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("email", "absentpw@otterworks.dev");
    body.put("displayName", "Absent Password");
    postRegister(body).andExpect(status().isBadRequest());
  }

  @Test
  void register_whitespaceOnlyPassword_returns400() throws Exception {
    postRegister(fields("blankpw@otterworks.dev", "        ", "Blank Password"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void register_absentDisplayName_returns400() throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("email", "absentdn@otterworks.dev");
    body.put("password", "password123");
    postRegister(body).andExpect(status().isBadRequest());
  }

  @Test
  void register_whitespaceOnlyDisplayName_returns400() throws Exception {
    postRegister(fields("blankdn@otterworks.dev", "password123", "   "))
        .andExpect(status().isBadRequest());
  }

  // --- Login: empty vs absent vs whitespace-only ---

  @Test
  void login_absentPassword_returns400() throws Exception {
    createUser("loginabsent@otterworks.dev");
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("email", "loginabsent@otterworks.dev");
    postLogin(body).andExpect(status().isBadRequest());
  }

  @Test
  void login_emptyPassword_returns400() throws Exception {
    createUser("loginempty@otterworks.dev");
    postLogin(loginFields("loginempty@otterworks.dev", "")).andExpect(status().isBadRequest());
  }

  @Test
  void login_whitespaceOnlyPassword_returns400() throws Exception {
    createUser("loginblank@otterworks.dev");
    postLogin(loginFields("loginblank@otterworks.dev", "   ")).andExpect(status().isBadRequest());
  }

  @Test
  void login_unknownEmail_returns400() throws Exception {
    postLogin(loginFields("nobody@otterworks.dev", "password123"))
        .andExpect(status().isBadRequest());
  }

  // --- Case-only email collisions ---

  /**
   * Email addresses are handled case-insensitively by essentially every mail provider, so
   * registering an address that differs from an existing one only by case creates a second account
   * for the same mailbox.
   */
  @Test
  @Disabled(
      "DEFECT: registration is case-sensitive on email. AuthService.register only checks"
          + " userRepository.existsByEmail(request.getEmail()) with the raw input and the address"
          + " is never normalised, so USER@otterworks.dev registers a second account alongside"
          + " user@otterworks.dev. Observed: 201 Created. Expected: 400 Bad Request.")
  void register_emailDifferingOnlyByCase_returns400() throws Exception {
    createUser("casecollision@otterworks.dev");

    postRegister(fields("CaseCollision@otterworks.dev", "password123", "Case Collision"))
        .andExpect(status().isBadRequest());
  }

  /** Counterpart of the registration collision: the owner of the mailbox cannot log back in. */
  @Test
  @Disabled(
      "DEFECT: login is case-sensitive on email. AuthService.login looks the user up with"
          + " userRepository.findByEmail(request.getEmail()) without normalising case, so a user"
          + " registered as caselogin@otterworks.dev cannot authenticate as"
          + " CaseLogin@otterworks.dev. Observed: 400 Bad Request. Expected: 200 OK.")
  void login_emailDifferingOnlyByCase_returns200() throws Exception {
    createUser("caselogin@otterworks.dev");

    postLogin(loginFields("CaseLogin@otterworks.dev", "password123")).andExpect(status().isOk());
  }

  // --- Change password ---

  @Test
  void changePassword_wrongCurrentPassword_returns400() throws Exception {
    String accessToken = registerAndGetAccessToken("wrongcurrent@otterworks.dev");

    Map<String, Object> body = new LinkedHashMap<>();
    body.put("currentPassword", "notmypassword");
    body.put("newPassword", "newpassword456");

    postChangePassword(accessToken, body).andExpect(status().isBadRequest());
  }

  @Test
  void changePassword_wrongCurrentPassword_leavesExistingPasswordUsable() throws Exception {
    String accessToken = registerAndGetAccessToken("stillworks@otterworks.dev");

    Map<String, Object> body = new LinkedHashMap<>();
    body.put("currentPassword", "notmypassword");
    body.put("newPassword", "newpassword456");
    postChangePassword(accessToken, body).andExpect(status().isBadRequest());

    postLogin(loginFields("stillworks@otterworks.dev", "password123")).andExpect(status().isOk());
  }

  @Test
  void changePassword_absentCurrentPassword_returns400() throws Exception {
    String accessToken = registerAndGetAccessToken("absentcurrent@otterworks.dev");

    Map<String, Object> body = new LinkedHashMap<>();
    body.put("newPassword", "newpassword456");

    postChangePassword(accessToken, body).andExpect(status().isBadRequest());
  }

  @Test
  void changePassword_whitespaceOnlyCurrentPassword_returns400() throws Exception {
    String accessToken = registerAndGetAccessToken("blankcurrent@otterworks.dev");

    Map<String, Object> body = new LinkedHashMap<>();
    body.put("currentPassword", "   ");
    body.put("newPassword", "newpassword456");

    postChangePassword(accessToken, body).andExpect(status().isBadRequest());
  }

  // --- Profile update ---

  /**
   * {@code UpdateProfileRequest.displayName} carries {@code @Size(min = 1)} but no
   * {@code @NotBlank}, unlike {@code RegisterRequest.displayName}.
   */
  @Test
  @Disabled(
      "DEFECT: profile update accepts a whitespace-only display name."
          + " UpdateProfileRequest.displayName declares @Size(min = 1, max = 100) but no @NotBlank,"
          + " so \"   \" passes validation and is persisted even though registration rejects the"
          + " same value. Observed: 200 OK with a blank display name. Expected: 400 Bad Request.")
  void updateProfile_whitespaceOnlyDisplayName_returns400() throws Exception {
    String accessToken = registerAndGetAccessToken("blankupdate@otterworks.dev");

    mockMvc
        .perform(
            put("/api/v1/auth/profile")
                .header("Authorization", "Bearer " + accessToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(Map.of("displayName", "   "))))
        .andExpect(status().isBadRequest());
  }

  private Map<String, Object> fields(String email, String password, String displayName) {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("email", email);
    body.put("password", password);
    body.put("displayName", displayName);
    return body;
  }

  private Map<String, Object> loginFields(String email, String password) {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("email", email);
    body.put("password", password);
    return body;
  }

  private ResultActions postRegister(Map<String, Object> body) throws Exception {
    return mockMvc.perform(
        post("/api/v1/auth/register")
            .contentType(MediaType.APPLICATION_JSON)
            .content(objectMapper.writeValueAsString(body)));
  }

  private ResultActions postLogin(Map<String, Object> body) throws Exception {
    return mockMvc.perform(
        post("/api/v1/auth/login")
            .contentType(MediaType.APPLICATION_JSON)
            .content(objectMapper.writeValueAsString(body)));
  }

  private ResultActions postChangePassword(String accessToken, Map<String, Object> body)
      throws Exception {
    return mockMvc.perform(
        post("/api/v1/auth/change-password")
            .header("Authorization", "Bearer " + accessToken)
            .contentType(MediaType.APPLICATION_JSON)
            .content(objectMapper.writeValueAsString(body)));
  }

  private void createUser(String email) throws Exception {
    postRegister(fields(email, "password123", "Negative Case User"))
        .andExpect(status().isCreated());
  }

  private String registerAndGetAccessToken(String email) throws Exception {
    MvcResult result =
        postRegister(fields(email, "password123", "Negative Case User"))
            .andExpect(status().isCreated())
            .andReturn();
    JsonNode json = objectMapper.readTree(result.getResponse().getContentAsString());
    return json.get("accessToken").asText();
  }
}
