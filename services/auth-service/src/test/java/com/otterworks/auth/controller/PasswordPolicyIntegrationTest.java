package com.otterworks.auth.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * Password-policy boundaries (WP-05).
 *
 * <p>The only policy the product states is {@code @Size(min = 8, max = 128)} on {@code
 * RegisterRequest.password} and {@code ChangePasswordRequest.newPassword}; display name is
 * {@code @Size(min = 1, max = 100)}. Every threshold below is exercised as {@code limit-1} / {@code
 * limit} / {@code limit+1}.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class PasswordPolicyIntegrationTest {

  private static final int MIN_PASSWORD = 8;
  private static final int MAX_PASSWORD = 128;
  private static final int MAX_DISPLAY_NAME = 100;

  /** Bytes of a password that BCrypt actually consumes; the rest is silently ignored. */
  private static final int BCRYPT_INPUT_LIMIT_BYTES = 72;

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;

  @BeforeEach
  void setUp() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  // ---------- register: password length trio ----------

  @Test
  void register_shouldRejectAPasswordOneCharBelowTheMinimum() throws Exception {
    register("pw-min-minus@otterworks.dev", "a".repeat(MIN_PASSWORD - 1))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("password")));
    assertThat(userRepository.existsByEmail("pw-min-minus@otterworks.dev")).isFalse();
  }

  @Test
  void register_shouldAcceptAPasswordExactlyAtTheMinimum() throws Exception {
    register("pw-min@otterworks.dev", "a".repeat(MIN_PASSWORD)).andExpect(status().isCreated());

    login("pw-min@otterworks.dev", "a".repeat(MIN_PASSWORD)).andExpect(status().isOk());
  }

  @Test
  void register_shouldAcceptAPasswordOneCharAboveTheMinimum() throws Exception {
    register("pw-min-plus@otterworks.dev", "a".repeat(MIN_PASSWORD + 1))
        .andExpect(status().isCreated());
  }

  @Test
  void register_shouldAcceptAPasswordOneCharBelowTheMaximum() throws Exception {
    register("pw-max-minus@otterworks.dev", "a".repeat(MAX_PASSWORD - 1))
        .andExpect(status().isCreated());
  }

  @Test
  void register_shouldAcceptAPasswordExactlyAtTheMaximum() throws Exception {
    register("pw-max@otterworks.dev", "a".repeat(MAX_PASSWORD)).andExpect(status().isCreated());

    login("pw-max@otterworks.dev", "a".repeat(MAX_PASSWORD)).andExpect(status().isOk());
  }

  @Test
  void register_shouldRejectAPasswordOneCharAboveTheMaximum() throws Exception {
    register("pw-max-plus@otterworks.dev", "a".repeat(MAX_PASSWORD + 1))
        .andExpect(status().isBadRequest());
    assertThat(userRepository.existsByEmail("pw-max-plus@otterworks.dev")).isFalse();
  }

  @Test
  void register_shouldRejectABlankPassword() throws Exception {
    register("pw-blank@otterworks.dev", "        ").andExpect(status().isBadRequest());
  }

  @Test
  void register_shouldRejectAMissingPassword() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    "{\"email\": \"pw-null@otterworks.dev\", \"displayName\": \"No Password\"}"))
        .andExpect(status().isBadRequest());
  }

  // ---------- change-password: same trio on the other DTO ----------

  @ParameterizedTest
  @CsvSource({"7, false", "8, true", "9, true", "127, true", "128, true", "129, false"})
  void changePassword_shouldEnforceTheSameLengthWindowAsRegistration(int length, boolean accepted)
      throws Exception {
    String email = "pw-change-" + length + "@otterworks.dev";
    String accessToken = registerAndGetAccessToken(email, "password123");
    String newPassword = "b".repeat(length);

    mockMvc
        .perform(
            post("/api/v1/auth/change-password")
                .header("Authorization", "Bearer " + accessToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    String.format(
                        "{\"currentPassword\": \"password123\", \"newPassword\": \"%s\"}",
                        newPassword)))
        .andExpect(accepted ? status().isNoContent() : status().isBadRequest());

    // The old password must still work whenever the change was rejected, and never afterwards.
    login(email, "password123").andExpect(accepted ? status().isBadRequest() : status().isOk());
  }

  @Test
  void changePassword_shouldRejectANewPasswordWhenTheCurrentOneIsWrong() throws Exception {
    String email = "pw-change-wrong@otterworks.dev";
    String accessToken = registerAndGetAccessToken(email, "password123");

    mockMvc
        .perform(
            post("/api/v1/auth/change-password")
                .header("Authorization", "Bearer " + accessToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    "{\"currentPassword\": \"not-the-password\", \"newPassword\":"
                        + " \"brandnewpassword\"}"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.message").value("Current password is incorrect"));

    login(email, "password123").andExpect(status().isOk());
  }

  // ---------- unicode ----------

  @Test
  void register_shouldAcceptACyrillicPasswordAndAuthenticateWithIt() throws Exception {
    String password = "\u043f\u0430\u0440\u043e\u043b\u044c123";
    register("pw-cyrillic@otterworks.dev", password).andExpect(status().isCreated());

    login("pw-cyrillic@otterworks.dev", password).andExpect(status().isOk());
    login("pw-cyrillic@otterworks.dev", "parol123").andExpect(status().isBadRequest());
  }

  @Test
  void register_shouldAcceptAnEmojiPasswordAndAuthenticateWithIt() throws Exception {
    String password = "\uD83E\uDDA6\uD83E\uDDA6\uD83E\uDDA6\uD83E\uDDA6xyzw";
    register("pw-emoji@otterworks.dev", password).andExpect(status().isCreated());

    login("pw-emoji@otterworks.dev", password).andExpect(status().isOk());
  }

  /**
   * {@code @Size} counts UTF-16 code units, so four astral-plane code points satisfy {@code min =
   * 8} while seven ASCII characters do not. Pinned as observed behaviour: the policy is a length
   * rule, not a strength rule.
   */
  @Test
  void register_shouldTreatFourEmojiAsEightCharactersForTheMinimum() throws Exception {
    String fourEmoji = "\uD83E\uDDA6".repeat(4);
    assertThat(fourEmoji).hasSize(MIN_PASSWORD);
    assertThat(fourEmoji.codePointCount(0, fourEmoji.length())).isEqualTo(4);

    register("pw-emoji-min@otterworks.dev", fourEmoji).andExpect(status().isCreated());
  }

  @Test
  void register_shouldRejectAMultiByteCharacterPasswordShorterThanTheMinimum() throws Exception {
    String sevenAccented = "\u00e9".repeat(MIN_PASSWORD - 1);
    assertThat(sevenAccented.getBytes(StandardCharsets.UTF_8)).hasSize(14);

    register("pw-accented-short@otterworks.dev", sevenAccented).andExpect(status().isBadRequest());
  }

  /**
   * DEFECT WP05-3 (judged genuine, not planted): passwords are not Unicode-normalised before
   * hashing, so the same user-visible password typed on a keyboard that emits NFD cannot log in
   * against a hash created from NFC. Pinned as observed behaviour.
   */
  @Test
  void login_currentlyRejectsTheNfdFormOfAnNfcPassword_seeDefectWp05x3() throws Exception {
    String nfc = "caf\u00e9passe";
    String nfd = "cafe\u0301passe";
    assertThat(nfc).isNotEqualTo(nfd);
    register("pw-nfc@otterworks.dev", nfc).andExpect(status().isCreated());

    login("pw-nfc@otterworks.dev", nfc).andExpect(status().isOk());
    login("pw-nfc@otterworks.dev", nfd).andExpect(status().isBadRequest());
  }

  /**
   * DEFECT WP05-4 (judged genuine, not planted — an inherited BCrypt property, not repo-specific):
   * BCrypt hashes at most the first 72 bytes of the password, so the policy's 128-character ceiling
   * is 56 characters of security theatre. Pinned as observed behaviour so that adopting a pre-hash
   * (or a shorter documented ceiling) turns this red on purpose.
   */
  @Test
  void login_currentlyAcceptsAPasswordDifferingOnlyPastByte72_seeDefectWp05x4() throws Exception {
    String prefix = "P".repeat(BCRYPT_INPUT_LIMIT_BYTES);
    String registered = prefix + "REAL-TAIL-0123456789";
    String forgery = prefix + "FAKE-TAIL-9876543210";
    assertThat(registered).isNotEqualTo(forgery).hasSameSizeAs(forgery);

    register("pw-bcrypt72@otterworks.dev", registered).andExpect(status().isCreated());

    login("pw-bcrypt72@otterworks.dev", forgery).andExpect(status().isOk());
  }

  @Test
  void login_shouldRejectAPasswordDifferingWithinTheFirst72Bytes() throws Exception {
    String registered = "Q".repeat(BCRYPT_INPUT_LIMIT_BYTES - 1) + "A" + "tail";
    String wrong = "Q".repeat(BCRYPT_INPUT_LIMIT_BYTES - 1) + "B" + "tail";

    register("pw-bcrypt71@otterworks.dev", registered).andExpect(status().isCreated());

    login("pw-bcrypt71@otterworks.dev", wrong).andExpect(status().isBadRequest());
  }

  // ---------- display name trio ----------

  @ParameterizedTest
  @CsvSource({"0, false", "1, true", "99, true", "100, true", "101, false"})
  void register_shouldEnforceTheDisplayNameLengthWindow(int length, boolean accepted)
      throws Exception {
    String email = "display-" + length + "@otterworks.dev";
    String body =
        String.format(
            "{\"email\": \"%s\", \"password\": \"password123\", \"displayName\": \"%s\"}",
            email, "n".repeat(length));

    mockMvc
        .perform(
            post("/api/v1/auth/register").contentType(MediaType.APPLICATION_JSON).content(body))
        .andExpect(accepted ? status().isCreated() : status().isBadRequest());

    assertThat(userRepository.existsByEmail(email)).isEqualTo(accepted);
    assertThat(MAX_DISPLAY_NAME).isEqualTo(100);
  }

  // ---------- helpers ----------

  private org.springframework.test.web.servlet.ResultActions register(String email, String password)
      throws Exception {
    String body =
        objectMapper
            .createObjectNode()
            .put("email", email)
            .put("password", password)
            .put("displayName", "WP05 Password User")
            .toString();
    return mockMvc.perform(
        post("/api/v1/auth/register").contentType(MediaType.APPLICATION_JSON).content(body));
  }

  private org.springframework.test.web.servlet.ResultActions login(String email, String password)
      throws Exception {
    String body =
        objectMapper.createObjectNode().put("email", email).put("password", password).toString();
    return mockMvc.perform(
        post("/api/v1/auth/login").contentType(MediaType.APPLICATION_JSON).content(body));
  }

  private String registerAndGetAccessToken(String email, String password) throws Exception {
    MvcResult result = register(email, password).andExpect(status().isCreated()).andReturn();
    JsonNode json = objectMapper.readTree(result.getResponse().getContentAsString());
    return json.get("accessToken").asText();
  }
}
