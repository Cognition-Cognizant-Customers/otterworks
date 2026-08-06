package com.otterworks.auth.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.otterworks.auth.dto.RegisterRequest;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import com.otterworks.auth.service.AuthService;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/**
 * Registration over the wire: password-policy boundaries as the API actually enforces them, email
 * casing, and duplicate registration (sequential and concurrent).
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class RegistrationPolicyIntegrationTest {

  private static final int PASSWORD_MIN = 8;
  private static final int PASSWORD_MAX = 128;

  @Autowired private MockMvc mockMvc;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;
  @Autowired private AuthService authService;

  @BeforeEach
  void resetDatabase() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  // ---------------------------------------------------------------- password boundaries

  @ParameterizedTest(name = "registering with a {0}-char password is rejected")
  @ValueSource(ints = {PASSWORD_MIN - 1, PASSWORD_MAX + 1})
  void register_passwordOutsideThePolicyIsRejected(int length) throws Exception {
    mockMvc
        .perform(registration("bounds" + length + "@otterworks.dev", "a".repeat(length)))
        .andExpect(status().isBadRequest());

    assertThat(userRepository.count()).isZero();
  }

  @ParameterizedTest(name = "registering with a {0}-char password succeeds")
  @ValueSource(ints = {PASSWORD_MIN, PASSWORD_MAX})
  void register_passwordOnThePolicyBoundaryIsAccepted(int length) throws Exception {
    mockMvc
        .perform(registration("bounds" + length + "@otterworks.dev", "a".repeat(length)))
        .andExpect(status().isCreated());

    assertThat(userRepository.count()).isEqualTo(1);
  }

  @Test
  void register_emptyPasswordIsRejected() throws Exception {
    mockMvc.perform(registration("empty@otterworks.dev", "")).andExpect(status().isBadRequest());
  }

  @Test
  void register_whitespaceOnlyPasswordIsRejected() throws Exception {
    mockMvc
        .perform(registration("blank@otterworks.dev", " ".repeat(PASSWORD_MIN + 2)))
        .andExpect(status().isBadRequest());
  }

  @Test
  void register_unicodePasswordRoundTripsThroughLogin() throws Exception {
    String password = "пароль-密码-🦦";

    mockMvc
        .perform(registration("unicode@otterworks.dev", password))
        .andExpect(status().isCreated());

    mockMvc.perform(login("unicode@otterworks.dev", password)).andExpect(status().isOk());
  }

  @Test
  void register_unicodePasswordDoesNotAuthenticateItsAsciiTransliteration() throws Exception {
    mockMvc
        .perform(registration("unicode-neg@otterworks.dev", "密码密码密码密码"))
        .andExpect(status().isCreated());

    mockMvc
        .perform(login("unicode-neg@otterworks.dev", "mimamima"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void register_longPasswordIsSilentlyTruncatedAtBcryptsSeventyTwoByteLimit() throws Exception {
    // FINDING (pinned, not fixed): the policy allows up to 128 characters but BCrypt only
    // consumes the first 72 bytes, so every character past byte 72 is decorative - the
    // 72-byte prefix of a 128-char password authenticates successfully.
    String password = "a".repeat(PASSWORD_MAX);
    mockMvc
        .perform(registration("truncation@otterworks.dev", password))
        .andExpect(status().isCreated());

    mockMvc
        .perform(login("truncation@otterworks.dev", password.substring(0, 72)))
        .andExpect(status().isOk());
    mockMvc
        .perform(login("truncation@otterworks.dev", password.substring(0, 71)))
        .andExpect(status().isBadRequest());
  }

  @Test
  @Disabled(
      "FINDING: BCrypt truncates at 72 bytes; enable when the policy caps at 72 bytes or the"
          + " encoder pre-hashes")
  void register_longPasswordShouldNotBeAuthenticatedByItsPrefix() throws Exception {
    String password = "a".repeat(PASSWORD_MAX);
    mockMvc
        .perform(registration("truncation-desired@otterworks.dev", password))
        .andExpect(status().isCreated());

    mockMvc
        .perform(login("truncation-desired@otterworks.dev", password.substring(0, 72)))
        .andExpect(status().isBadRequest());
  }

  // ---------------------------------------------------------------- display name boundaries

  @ParameterizedTest(name = "registering with a {0}-char display name is rejected")
  @ValueSource(ints = {0, 101})
  void register_displayNameOutsideThePolicyIsRejected(int length) throws Exception {
    String body =
        String.format(
            "{\"email\": \"dn%d@otterworks.dev\", \"password\": \"password123\","
                + " \"displayName\": \"%s\"}",
            length, "n".repeat(length));

    mockMvc
        .perform(
            post("/api/v1/auth/register").contentType(MediaType.APPLICATION_JSON).content(body))
        .andExpect(status().isBadRequest());
  }

  @ParameterizedTest(name = "registering with a {0}-char display name succeeds")
  @ValueSource(ints = {1, 100})
  void register_displayNameOnThePolicyBoundaryIsAccepted(int length) throws Exception {
    String body =
        String.format(
            "{\"email\": \"dn%d@otterworks.dev\", \"password\": \"password123\","
                + " \"displayName\": \"%s\"}",
            length, "n".repeat(length));

    mockMvc
        .perform(
            post("/api/v1/auth/register").contentType(MediaType.APPLICATION_JSON).content(body))
        .andExpect(status().isCreated());
  }

  // ---------------------------------------------------------------- email casing

  @Test
  void register_emailIsStoredVerbatimAndLoginIsCaseSensitive() throws Exception {
    // FINDING (pinned, not fixed): neither registration nor login normalises the address, so
    // "User@x.com" cannot log in as "user@x.com".
    mockMvc
        .perform(registration("Casing@otterworks.dev", "password123"))
        .andExpect(status().isCreated());

    mockMvc
        .perform(login("casing@otterworks.dev", "password123"))
        .andExpect(status().isBadRequest());
    mockMvc.perform(login("Casing@otterworks.dev", "password123")).andExpect(status().isOk());
  }

  @Test
  void register_differentCasingsOfTheSameAddressCreateTwoAccounts() throws Exception {
    // FINDING (pinned, not fixed): existsByEmail is an exact match, so the uniqueness rule is
    // per byte sequence rather than per mailbox.
    mockMvc
        .perform(registration("Dual@otterworks.dev", "password123"))
        .andExpect(status().isCreated());
    mockMvc
        .perform(registration("dual@otterworks.dev", "password123"))
        .andExpect(status().isCreated());

    assertThat(userRepository.count()).isEqualTo(2);
  }

  @Test
  @Disabled("FINDING: email is not normalised; enable when registration lower-cases the address")
  void register_shouldTreatEmailAddressesAsCaseInsensitive() throws Exception {
    mockMvc
        .perform(registration("Normalise@otterworks.dev", "password123"))
        .andExpect(status().isCreated());

    mockMvc
        .perform(registration("normalise@otterworks.dev", "password123"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void register_emailWithAnOverLongLocalPartIsRejected() throws Exception {
    // @Email enforces the RFC 64-character local-part limit, so this is a clean 400.
    mockMvc
        .perform(registration("e".repeat(65) + "@otterworks.dev", "password123"))
        .andExpect(status().isBadRequest());

    assertThat(userRepository.count()).isZero();
  }

  @Test
  void register_emailLongerThanTheColumnCurrentlyReturns500InsteadOf400() throws Exception {
    // FINDING (pinned, not fixed): RegisterRequest.email has no @Size while User.email is
    // varchar(255). An RFC-shaped but over-long address (legal 64-char local part, long
    // domain) passes validation and then blows up in the persistence layer.
    String email = rfcEmailWithDomainLabels(63, 63, 63);
    assertThat(email.length()).isGreaterThan(255);

    mockMvc.perform(registration(email, "password123")).andExpect(status().is5xxServerError());

    assertThat(userRepository.count()).isZero();
  }

  @Test
  void register_emailAtTheColumnLimitIsAccepted() throws Exception {
    String email = rfcEmailWithDomainLabels(63, 63, 62);
    assertThat(email).hasSize(255);

    mockMvc.perform(registration(email, "password123")).andExpect(status().isCreated());
  }

  // ---------------------------------------------------------------- duplicate registration

  @Test
  void register_duplicateEmailIsRejectedAndLeavesTheOriginalAccountIntact() throws Exception {
    mockMvc
        .perform(registration("dupe@otterworks.dev", "password123"))
        .andExpect(status().isCreated());

    mockMvc
        .perform(registration("dupe@otterworks.dev", "different456"))
        .andExpect(status().isBadRequest());

    assertThat(userRepository.count()).isEqualTo(1);
    mockMvc.perform(login("dupe@otterworks.dev", "password123")).andExpect(status().isOk());
  }

  @Test
  void register_concurrentDuplicateRegistrationsCreateExactlyOneAccount() throws Exception {
    // The existsByEmail check and the insert are not atomic, so two simultaneous callers can
    // both pass the check; the unique constraint on users.email is what actually holds the
    // line. Whichever way the race lands, exactly one account must exist and exactly one
    // caller must succeed.
    String email = "race@otterworks.dev";
    int callers = 2;
    CyclicBarrier startLine = new CyclicBarrier(callers);
    AtomicInteger succeeded = new AtomicInteger();
    AtomicInteger failed = new AtomicInteger();

    ExecutorService pool = Executors.newFixedThreadPool(callers);
    try {
      Callable<Void> attempt =
          () -> {
            startLine.await(30, TimeUnit.SECONDS);
            try {
              authService.register(registerRequest(email));
              succeeded.incrementAndGet();
            } catch (RuntimeException expectedForTheLoser) {
              failed.incrementAndGet();
            }
            return null;
          };

      for (Future<Void> future : pool.invokeAll(List.of(attempt, attempt))) {
        future.get(30, TimeUnit.SECONDS);
      }
    } finally {
      pool.shutdownNow();
    }

    assertThat(succeeded.get()).isEqualTo(1);
    assertThat(failed.get()).isEqualTo(1);
    assertThat(userRepository.count()).isEqualTo(1);
  }

  @Test
  void register_repeatedIdenticalSubmissionsAreNotIdempotent() throws Exception {
    // Pins the double-submit contract: the second identical POST is an error, not a no-op
    // that returns the existing session.
    mockMvc
        .perform(registration("doublesubmit@otterworks.dev", "password123"))
        .andExpect(status().isCreated());
    mockMvc
        .perform(registration("doublesubmit@otterworks.dev", "password123"))
        .andExpect(status().isBadRequest());

    assertThat(userRepository.count()).isEqualTo(1);
  }

  // ---------------------------------------------------------------- helpers

  /** Builds a syntactically valid address with a 64-character local part and the given labels. */
  private static String rfcEmailWithDomainLabels(int... labelLengths) {
    StringBuilder domain = new StringBuilder();
    for (int length : labelLengths) {
      if (domain.length() > 0) {
        domain.append('.');
      }
      domain.append("d".repeat(length));
    }
    return "e".repeat(64) + "@" + domain;
  }

  private static RegisterRequest registerRequest(String email) {
    RegisterRequest request = new RegisterRequest();
    request.setEmail(email);
    request.setPassword("password123");
    request.setDisplayName("Race Caller");
    return request;
  }

  private MockHttpServletRequestBuilder registration(String email, String password) {
    String body =
        String.format(
            "{\"email\": \"%s\", \"password\": \"%s\", \"displayName\": \"Policy User\"}",
            email, password);
    return post("/api/v1/auth/register").contentType(MediaType.APPLICATION_JSON).content(body);
  }

  private MockHttpServletRequestBuilder login(String email, String password) {
    String body = String.format("{\"email\": \"%s\", \"password\": \"%s\"}", email, password);
    return post("/api/v1/auth/login").contentType(MediaType.APPLICATION_JSON).content(body);
  }
}
