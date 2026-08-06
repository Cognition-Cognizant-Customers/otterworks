package com.otterworks.auth.dto;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * Bean-validation boundaries for the credentials DTOs.
 *
 * <p>Constants are taken from the annotations themselves: {@code RegisterRequest.password} is
 * {@code @Size(min = 8, max = 128)}, {@code displayName} is {@code @Size(min = 1, max = 100)}, and
 * {@code ChangePasswordRequest.newPassword} is {@code @Size(min = 8, max = 128)}. Each numeric
 * threshold is exercised at limit-1 / limit / limit+1.
 */
class PasswordPolicyValidationTest {

  private static final int PASSWORD_MIN = 8;
  private static final int PASSWORD_MAX = 128;
  private static final int DISPLAY_NAME_MIN = 1;
  private static final int DISPLAY_NAME_MAX = 100;

  private static ValidatorFactory validatorFactory;
  private static Validator validator;

  @BeforeAll
  static void openValidator() {
    validatorFactory = Validation.buildDefaultValidatorFactory();
    validator = validatorFactory.getValidator();
  }

  @AfterAll
  static void closeValidator() {
    validatorFactory.close();
  }

  // ---------------------------------------------------------------- password length

  @ParameterizedTest(name = "password of {0} chars is rejected below the minimum")
  @ValueSource(ints = {0, 1, PASSWORD_MIN - 1})
  void registerPassword_belowMinLength_isRejected(int length) {
    RegisterRequest request = register(repeat('a', length));

    assertThat(fieldsWithViolations(request)).contains("password");
  }

  @ParameterizedTest(name = "password of {0} chars is accepted")
  @ValueSource(ints = {PASSWORD_MIN, PASSWORD_MIN + 1, PASSWORD_MAX - 1, PASSWORD_MAX})
  void registerPassword_withinBounds_isAccepted(int length) {
    RegisterRequest request = register(repeat('a', length));

    assertThat(fieldsWithViolations(request)).doesNotContain("password");
  }

  @ParameterizedTest(name = "password of {0} chars is rejected above the maximum")
  @ValueSource(ints = {PASSWORD_MAX + 1, PASSWORD_MAX + 64})
  void registerPassword_aboveMaxLength_isRejected(int length) {
    RegisterRequest request = register(repeat('a', length));

    assertThat(fieldsWithViolations(request)).contains("password");
  }

  @Test
  void registerPassword_null_isRejected() {
    RegisterRequest request = register(null);

    assertThat(fieldsWithViolations(request)).contains("password");
  }

  @Test
  void registerPassword_empty_isRejected() {
    RegisterRequest request = register("");

    assertThat(fieldsWithViolations(request)).contains("password");
  }

  @ParameterizedTest(name = "whitespace-only password of {0} chars is rejected by @NotBlank")
  @ValueSource(ints = {PASSWORD_MIN, PASSWORD_MIN + 4, PASSWORD_MAX})
  void registerPassword_whitespaceOnly_isRejectedEvenWhenLongEnough(int length) {
    RegisterRequest request = register(repeat(' ', length));

    assertThat(fieldsWithViolations(request)).contains("password");
  }

  @Test
  void registerPassword_tabsAndNewlinesOnly_isRejected() {
    RegisterRequest request = register("\t\n\t\n\t\n\t\n");

    assertThat(fieldsWithViolations(request)).contains("password");
  }

  @Test
  void registerPassword_leadingAndTrailingWhitespaceAroundContent_isAccepted() {
    // @NotBlank only requires one non-whitespace char; padding is preserved, not trimmed.
    RegisterRequest request = register("   abc   ");

    assertThat(fieldsWithViolations(request)).doesNotContain("password");
  }

  // ---------------------------------------------------------------- unicode passwords

  @ParameterizedTest(name = "unicode password {0} is accepted")
  @ValueSource(
      strings = {
        "пароль12", // Cyrillic, 8 code units
        "密码密码密码密码", // CJK, 8 code units
        "Ωμέγα123", // Greek, 8 code units
        "café-passé", // accented Latin, 10 code units
        "🦦🦦🦦🦦" // 4 emoji = 8 UTF-16 code units
      })
  void registerPassword_unicode_isAcceptedWhenLongEnoughInCodeUnits(String password) {
    RegisterRequest request = register(password);

    assertThat(fieldsWithViolations(request)).doesNotContain("password");
  }

  @Test
  void registerPassword_emojiCountAsTwoCodeUnitsEach() {
    // Documents that @Size counts UTF-16 code units, not user-perceived characters: three
    // emoji (6 code units) are below the minimum while four (8 code units) are not.
    assertThat(fieldsWithViolations(register("🦦🦦🦦"))).contains("password");
    assertThat(fieldsWithViolations(register("🦦🦦🦦🦦"))).doesNotContain("password");
  }

  @Test
  void registerPassword_unicodeAtMaxLength_isAcceptedAndOneOverIsRejected() {
    assertThat(fieldsWithViolations(register(repeat('密', PASSWORD_MAX))))
        .doesNotContain("password");
    assertThat(fieldsWithViolations(register(repeat('密', PASSWORD_MAX + 1)))).contains("password");
  }

  // ---------------------------------------------------------------- display name

  @ParameterizedTest(name = "displayName of {0} chars is rejected")
  @ValueSource(ints = {DISPLAY_NAME_MIN - 1, DISPLAY_NAME_MAX + 1})
  void registerDisplayName_outsideBounds_isRejected(int length) {
    RegisterRequest request = register("password123");
    request.setDisplayName(repeat('n', length));

    assertThat(fieldsWithViolations(request)).contains("displayName");
  }

  @ParameterizedTest(name = "displayName of {0} chars is accepted")
  @ValueSource(
      ints = {DISPLAY_NAME_MIN, DISPLAY_NAME_MIN + 1, DISPLAY_NAME_MAX - 1, DISPLAY_NAME_MAX})
  void registerDisplayName_withinBounds_isAccepted(int length) {
    RegisterRequest request = register("password123");
    request.setDisplayName(repeat('n', length));

    assertThat(fieldsWithViolations(request)).doesNotContain("displayName");
  }

  @Test
  void registerDisplayName_whitespaceOnly_isRejected() {
    RegisterRequest request = register("password123");
    request.setDisplayName("     ");

    assertThat(fieldsWithViolations(request)).contains("displayName");
  }

  // ---------------------------------------------------------------- email

  @ParameterizedTest(name = "email \"{0}\" is rejected")
  @ValueSource(strings = {"", "   ", "not-an-email", "no-at-sign.dev", "@otterworks.dev"})
  void registerEmail_malformed_isRejected(String email) {
    RegisterRequest request = register("password123");
    request.setEmail(email);

    assertThat(fieldsWithViolations(request)).contains("email");
  }

  @Test
  void registerEmail_null_isRejected() {
    RegisterRequest request = register("password123");
    request.setEmail(null);

    assertThat(fieldsWithViolations(request)).contains("email");
  }

  @Test
  void registerEmail_overLongLocalPartIsRejectedByTheRfcRules() {
    // @Email delegates to Hibernate Validator, which enforces the RFC local-part limit of 64
    // characters even though the DTO declares no @Size.
    RegisterRequest request = register("password123");
    request.setEmail(repeat('e', 65) + "@otterworks.dev");

    assertThat(fieldsWithViolations(request)).contains("email");
  }

  @Test
  void registerEmail_localPartAtTheRfcLimitIsAccepted() {
    RegisterRequest request = register("password123");
    request.setEmail(repeat('e', 64) + "@otterworks.dev");

    assertThat(fieldsWithViolations(request)).doesNotContain("email");
  }

  @Test
  void registerEmail_hasNoTotalLengthCeilingEvenThoughTheColumnIs255() {
    // FINDING: RegisterRequest.email carries @Email/@NotBlank but no @Size, while User.email
    // is @Column(length = 255). An RFC-shaped address can still exceed 255 characters through
    // its domain, so it passes bean validation and fails in the persistence layer instead of
    // returning 400. Pinned end-to-end by RegistrationPolicyIntegrationTest.
    RegisterRequest request = register("password123");
    String email =
        repeat('e', 64) + "@" + repeat('d', 63) + "." + repeat('d', 63) + "." + repeat('d', 63);
    assertThat(email.length()).isGreaterThan(255);
    request.setEmail(email);

    assertThat(fieldsWithViolations(request)).doesNotContain("email");
  }

  // ---------------------------------------------------------------- change password

  @ParameterizedTest(name = "newPassword of {0} chars is rejected")
  @ValueSource(ints = {0, PASSWORD_MIN - 1, PASSWORD_MAX + 1})
  void changePassword_newPasswordOutsideBounds_isRejected(int length) {
    ChangePasswordRequest request = changePassword("currentPassword", repeat('b', length));

    assertThat(fieldsWithViolations(request)).contains("newPassword");
  }

  @ParameterizedTest(name = "newPassword of {0} chars is accepted")
  @ValueSource(ints = {PASSWORD_MIN, PASSWORD_MIN + 1, PASSWORD_MAX - 1, PASSWORD_MAX})
  void changePassword_newPasswordWithinBounds_isAccepted(int length) {
    ChangePasswordRequest request = changePassword("currentPassword", repeat('b', length));

    assertThat(fieldsWithViolations(request)).doesNotContain("newPassword");
  }

  @ParameterizedTest(name = "currentPassword \"{0}\" is rejected")
  @CsvSource(
      value = {"''", "'   '", "NULL"},
      nullValues = "NULL")
  void changePassword_blankCurrentPassword_isRejected(String currentPassword) {
    ChangePasswordRequest request = changePassword(currentPassword, "newPassword123");

    assertThat(fieldsWithViolations(request)).contains("currentPassword");
  }

  @Test
  void changePassword_currentPasswordHasNoLengthCeiling() {
    // currentPassword is only @NotBlank: a one-character value is structurally valid and is
    // rejected later by the encoder, not by validation.
    ChangePasswordRequest request = changePassword("x", "newPassword123");

    assertThat(fieldsWithViolations(request)).doesNotContain("currentPassword");
  }

  @Test
  void changePassword_reusingTheCurrentPasswordIsStructurallyValid() {
    // FINDING: there is no "new password must differ from the current one" rule anywhere in
    // the DTO or in AuthService.changePassword.
    ChangePasswordRequest request = changePassword("password123", "password123");

    assertThat(fieldsWithViolations(request)).isEmpty();
  }

  // ---------------------------------------------------------------- login request

  @ParameterizedTest(name = "login password \"{0}\" is rejected")
  @CsvSource(
      value = {"''", "'   '", "NULL"},
      nullValues = "NULL")
  void login_blankPassword_isRejected(String password) {
    LoginRequest request = new LoginRequest();
    request.setEmail("user@otterworks.dev");
    request.setPassword(password);

    assertThat(fieldsWithViolations(request)).contains("password");
  }

  @Test
  void login_hasNoLengthPolicyAtAll() {
    // Deliberate asymmetry worth pinning: login accepts a 1-char password so that accounts
    // created before the current policy can still authenticate.
    LoginRequest request = new LoginRequest();
    request.setEmail("user@otterworks.dev");
    request.setPassword("x");

    assertThat(fieldsWithViolations(request)).isEmpty();
  }

  // ---------------------------------------------------------------- helpers

  private static RegisterRequest register(String password) {
    RegisterRequest request = new RegisterRequest();
    request.setEmail("user@otterworks.dev");
    request.setPassword(password);
    request.setDisplayName("Policy User");
    return request;
  }

  private static ChangePasswordRequest changePassword(String current, String next) {
    ChangePasswordRequest request = new ChangePasswordRequest();
    request.setCurrentPassword(current);
    request.setNewPassword(next);
    return request;
  }

  private static <T> Set<String> fieldsWithViolations(T bean) {
    Set<ConstraintViolation<T>> violations = validator.validate(bean);
    return violations.stream().map(v -> v.getPropertyPath().toString()).collect(Collectors.toSet());
  }

  private static String repeat(char c, int times) {
    return String.valueOf(c).repeat(times);
  }
}
