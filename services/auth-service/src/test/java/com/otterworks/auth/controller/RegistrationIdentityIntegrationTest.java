package com.otterworks.auth.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.auth.dto.RegisterRequest;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import com.otterworks.auth.service.AuthService;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.ResultActions;

/**
 * Registration identity rules (WP-05): email casing, duplicate handling — including a concurrent
 * duplicate-register race — and the email length boundary.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class RegistrationIdentityIntegrationTest {

  /** {@code users.email} is {@code VARCHAR(255)} in V1__create_users_table.sql. */
  private static final int MAX_EMAIL_COLUMN = 255;

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;
  @Autowired private AuthService authService;

  @BeforeEach
  void setUp() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  // ---------- positive ----------

  @Test
  void register_shouldPersistTheEmailExactlyAsSubmitted() throws Exception {
    register("Exact.Case@Otterworks.dev").andExpect(status().isCreated());

    assertThat(userRepository.findByEmail("Exact.Case@Otterworks.dev")).isPresent();
  }

  @ParameterizedTest
  @ValueSource(
      strings = {
        "plus+tag@otterworks.dev",
        "dotted.name@otterworks.dev",
        "dashed-name@sub.otterworks.dev",
        "_underscore@otterworks.dev"
      })
  void register_shouldAcceptValidEmailShapes(String email) throws Exception {
    register(email).andExpect(status().isCreated());
  }

  // ---------- negative: duplicates ----------

  @Test
  void register_shouldRejectAnExactDuplicateEmail() throws Exception {
    register("dupe@otterworks.dev").andExpect(status().isCreated());

    register("dupe@otterworks.dev")
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.message").value("Email already registered"));

    assertThat(userRepository.findAll()).hasSize(1);
  }

  @Test
  void register_shouldNotLeakWhetherTheExistingAccountsPasswordMatches() throws Exception {
    register("dupe-pw@otterworks.dev", "password123").andExpect(status().isCreated());

    register("dupe-pw@otterworks.dev", "totallyDifferent!")
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.message").value("Email already registered"));

    // The first account's credentials are untouched by the rejected attempt.
    login("dupe-pw@otterworks.dev", "password123").andExpect(status().isOk());
  }

  @ParameterizedTest
  @ValueSource(strings = {"not-an-email", "no-at-sign.dev", "two@@otterworks.dev", " "})
  void register_shouldRejectMalformedEmails(String email) throws Exception {
    register(email).andExpect(status().isBadRequest());
    assertThat(userRepository.findAll()).isEmpty();
  }

  // ---------- boundary: email length ----------

  /** Hibernate Validator's {@code @Email} caps the local part at RFC 5321's 64 characters. */
  @ParameterizedTest
  @CsvSource({"63, 201", "64, 201", "65, 400"})
  void register_shouldEnforceTheLocalPartLimit(int localPartLength, int expectedStatus)
      throws Exception {
    String email = "e".repeat(localPartLength) + "@otterworks.dev";

    register(email).andExpect(status().is(expectedStatus));
  }

  /**
   * DEFECT WP05-7 (judged genuine, not planted): {@code RegisterRequest.email} carries
   * {@code @Email} but no {@code @Size}, while {@code users.email} is {@code VARCHAR(255)}. One
   * character past the column width the request is not rejected as invalid input — it reaches the
   * database and comes back as {@code 500 Internal Server Error}. Pinned as observed behaviour;
   * adding {@code @Size(max = 255)} to the DTO would turn the third row into a 400 on purpose.
   */
  @ParameterizedTest
  @CsvSource({"254, 201", "255, 201", "256, 500"})
  void register_shouldHandleEmailsAroundTheColumnWidth_seeDefectWp05x7(
      int totalLength, int expectedStatus) throws Exception {
    String email = emailOfLength(totalLength);
    assertThat(email).hasSize(totalLength);

    register(email).andExpect(status().is(expectedStatus));

    assertThat(userRepository.existsByEmail(email)).isEqualTo(totalLength <= MAX_EMAIL_COLUMN);
  }

  /** {@code local(64)@label.label...} — every DNS label kept at or below 63 characters. */
  private static String emailOfLength(int totalLength) {
    StringBuilder domain = new StringBuilder();
    int domainLength = totalLength - 65;
    while (domain.length() < domainLength) {
      int remaining = domainLength - domain.length();
      if (!domain.isEmpty()) {
        domain.append('.');
        remaining--;
      }
      domain.append("d".repeat(Math.min(63, remaining)));
    }
    return "e".repeat(64) + "@" + domain;
  }

  // ---------- email casing ----------

  /**
   * DEFECT WP05-5 (judged genuine, not planted): email comparison is byte-exact. {@code
   * UserRepository.findByEmail} / {@code existsByEmail} are case-sensitive and nothing normalises
   * the address on the way in, so {@code User@x.dev} and {@code user@x.dev} are two distinct
   * accounts and a user who capitalises their address at the login screen is told their credentials
   * are invalid. Pinned as observed behaviour; the desired behaviour is the disabled test below.
   */
  @Test
  void login_currentlyRejectsADifferentlyCasedEmail_seeDefectWp05x5() throws Exception {
    register("Casing.User@Otterworks.dev", "password123").andExpect(status().isCreated());

    login("Casing.User@Otterworks.dev", "password123").andExpect(status().isOk());
    login("casing.user@otterworks.dev", "password123")
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.message").value("Invalid credentials"));
  }

  /** Second half of DEFECT WP05-5: the duplicate check is case-sensitive too. */
  @Test
  void register_currentlyAllowsTwoAccountsDifferingOnlyInCase_seeDefectWp05x5() throws Exception {
    register("Twin@otterworks.dev").andExpect(status().isCreated());

    register("twin@otterworks.dev").andExpect(status().isCreated());

    assertThat(userRepository.findAll()).hasSize(2);
  }

  @org.junit.jupiter.api.Disabled(
      "DEFECT WP05-5: emails are not case-normalised. Test-only work package; enable once the"
          + " product lower-cases addresses on registration and lookup.")
  @Test
  void register_shouldTreatEmailsAsCaseInsensitive() throws Exception {
    register("Twin.Desired@otterworks.dev").andExpect(status().isCreated());

    register("twin.desired@otterworks.dev").andExpect(status().isBadRequest());
    login("twin.desired@otterworks.dev", "password123").andExpect(status().isOk());
  }

  /** Third face of DEFECT WP05-5: the authenticated lookup route is case-sensitive as well. */
  @Test
  void lookup_currentlyMissesADifferentlyCasedEmail_seeDefectWp05x5() throws Exception {
    MvcResult result =
        register("Lookup.User@Otterworks.dev").andExpect(status().isCreated()).andReturn();
    String accessToken =
        objectMapper
            .readTree(result.getResponse().getContentAsString())
            .get("accessToken")
            .asText();

    mockMvc
        .perform(
            get("/api/v1/auth/users/lookup")
                .header("Authorization", "Bearer " + accessToken)
                .param("email", "Lookup.User@Otterworks.dev"))
        .andExpect(status().isOk());

    mockMvc
        .perform(
            get("/api/v1/auth/users/lookup")
                .header("Authorization", "Bearer " + accessToken)
                .param("email", "lookup.user@otterworks.dev"))
        .andExpect(status().isBadRequest());
  }

  // ---------- concurrency / idempotency ----------

  /**
   * Two registrations of the same address, released from a barrier at the same instant. Whichever
   * interleaving wins, the unique constraint on {@code users.email} must leave exactly one row and
   * exactly one caller must fail — the {@code existsByEmail}-then-{@code save} check in {@code
   * AuthService.register} is a check-then-act, so the constraint is the real guard.
   */
  @Test
  void register_shouldCreateExactlyOneAccountWhenTheSameEmailRacesWithItself() throws Exception {
    String email = "race@otterworks.dev";
    int racers = 2;
    CyclicBarrier startLine = new CyclicBarrier(racers);
    ExecutorService pool = Executors.newFixedThreadPool(racers);

    List<Future<Throwable>> results = new ArrayList<>();
    try {
      for (int i = 0; i < racers; i++) {
        results.add(pool.submit(registerAt(startLine, email)));
      }
      pool.shutdown();
      assertThat(pool.awaitTermination(30, TimeUnit.SECONDS)).isTrue();
    } finally {
      pool.shutdownNow();
    }

    List<Throwable> failures = new ArrayList<>();
    for (Future<Throwable> result : results) {
      Throwable thrown = result.get();
      if (thrown != null) {
        failures.add(thrown);
      }
    }

    assertThat(failures).as("exactly one racer must lose").hasSize(1);
    assertThat(userRepository.findAll()).hasSize(1);
    assertThat(userRepository.findByEmail(email)).isPresent();
  }

  @Test
  void register_shouldStillRejectTheDuplicateAfterTheRaceHasSettled() throws Exception {
    String email = "race-then-serial@otterworks.dev";
    register(email).andExpect(status().isCreated());

    register(email).andExpect(status().isBadRequest());
    register(email).andExpect(status().isBadRequest());

    assertThat(userRepository.findAll()).hasSize(1);
  }

  @Test
  void login_shouldBeRepeatableWithoutCreatingExtraAccounts() throws Exception {
    register("repeat-login@otterworks.dev").andExpect(status().isCreated());

    for (int attempt = 0; attempt < 3; attempt++) {
      login("repeat-login@otterworks.dev", "password123").andExpect(status().isOk());
    }

    assertThat(userRepository.findAll()).hasSize(1);
    assertThat(refreshTokenRepository.findAll()).hasSize(4);
  }

  // ---------- helpers ----------

  private Callable<Throwable> registerAt(CyclicBarrier startLine, String email) {
    return () -> {
      RegisterRequest request = new RegisterRequest();
      request.setEmail(email);
      request.setPassword("password123");
      request.setDisplayName("Racer");
      try {
        startLine.await(30, TimeUnit.SECONDS);
        authService.register(request);
        return null;
      } catch (Exception e) {
        return e;
      }
    };
  }

  private ResultActions register(String email) throws Exception {
    return register(email, "password123");
  }

  private ResultActions register(String email, String password) throws Exception {
    String body =
        objectMapper
            .createObjectNode()
            .put("email", email)
            .put("password", password)
            .put("displayName", "WP05 Identity User")
            .toString();
    return mockMvc.perform(
        post("/api/v1/auth/register").contentType(MediaType.APPLICATION_JSON).content(body));
  }

  private ResultActions login(String email, String password) throws Exception {
    String body =
        objectMapper.createObjectNode().put("email", email).put("password", password).toString();
    return mockMvc.perform(
        post("/api/v1/auth/login").contentType(MediaType.APPLICATION_JSON).content(body));
  }
}
