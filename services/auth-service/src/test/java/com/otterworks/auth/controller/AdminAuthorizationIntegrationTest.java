package com.otterworks.auth.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.auth.entity.User;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import com.otterworks.auth.testsupport.JwtFixtures;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * Authorization negatives for the admin-only route {@code GET /api/v1/auth/users} and its
 * authenticated-only neighbours (WP-05).
 *
 * <p>{@code SecurityConfig} guards {@code /api/v1/auth/users/**} with {@code hasRole("ADMIN")} and
 * {@code AuthController.listUsers} repeats the rule with {@code @PreAuthorize}. Every case below
 * drives the real filter chain through MockMvc.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AdminAuthorizationIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;
  @Autowired private PasswordEncoder passwordEncoder;

  @BeforeEach
  void setUp() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  // ---------- positive ----------

  @Test
  void listUsers_shouldSucceedForAnAdminToken() throws Exception {
    String adminToken = tokenFor(createUser("authz-admin@otterworks.dev", User.Role.ADMIN));

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + adminToken))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.content").isArray());
  }

  @Test
  void listUsers_shouldSucceedForATokenCarryingAdminAmongSeveralRoles() throws Exception {
    User admin =
        createUser("authz-multi@otterworks.dev", User.Role.USER, User.Role.EDITOR, User.Role.ADMIN);

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + tokenFor(admin)))
        .andExpect(status().isOk());
  }

  // ---------- negative: role ----------

  @Test
  void listUsers_shouldRejectAUserRoleToken() throws Exception {
    String userToken = tokenFor(createUser("authz-user@otterworks.dev", User.Role.USER));

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + userToken))
        .andExpect(status().isForbidden());
  }

  @ParameterizedTest
  @ValueSource(strings = {"USER", "EDITOR", "OWNER"})
  void listUsers_shouldRejectEveryNonAdminRole(String role) throws Exception {
    String token = JwtFixtures.accessTokenWithRoles(UUID.randomUUID().toString(), List.of(role));

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void listUsers_shouldRejectALowercasedAdminRoleClaim() throws Exception {
    String token = JwtFixtures.accessTokenWithRoles(UUID.randomUUID().toString(), List.of("admin"));

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void listUsers_shouldRejectARolesClaimAlreadyPrefixedWithRoleUnderscore() throws Exception {
    String token =
        JwtFixtures.accessTokenWithRoles(UUID.randomUUID().toString(), List.of("ROLE_ADMIN"));

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void listUsers_shouldRejectATokenWithNoRolesClaim() throws Exception {
    String token = JwtFixtures.accessTokenWithoutRolesClaim(UUID.randomUUID().toString());

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void listUsers_shouldRejectATokenWithAnEmptyRolesClaim() throws Exception {
    String token = JwtFixtures.accessTokenWithRoles(UUID.randomUUID().toString(), List.of());

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  // ---------- negative: credential shape ----------

  @Test
  void listUsers_shouldRejectAnUnauthenticatedRequest() throws Exception {
    mockMvc.perform(get("/api/v1/auth/users")).andExpect(status().isForbidden());
  }

  @ParameterizedTest
  @ValueSource(
      strings = {"", "Bearer", "Bearer ", "bearer forged", "Basic YWRtaW46YWRtaW4=", "Token abc"})
  void listUsers_shouldRejectMalformedAuthorizationHeaders(String header) throws Exception {
    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", header))
        .andExpect(status().isForbidden());
  }

  @Test
  void listUsers_shouldRejectAnAdminTokenSignedWithAnotherKey() throws Exception {
    createUser("authz-foreign@otterworks.dev", User.Role.ADMIN);
    String forged =
        JwtFixtures.accessTokenSignedWithForeignKey(UUID.randomUUID().toString(), List.of("ADMIN"));

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + forged))
        .andExpect(status().isForbidden());
  }

  /** The roles claim says {@code ADMIN}, so expiry is the only thing that can cause the 403. */
  @Test
  void listUsers_shouldRejectAnExpiredAdminToken() throws Exception {
    Instant now = Instant.now();
    String userId = UUID.randomUUID().toString();
    String expired =
        JwtFixtures.accessToken(
            userId, List.of("ADMIN"), now.minusSeconds(7200), now.minusSeconds(60));
    String live = JwtFixtures.accessToken(userId, List.of("ADMIN"), now, now.plusSeconds(3600));

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + expired))
        .andExpect(status().isForbidden());

    // The same claims, unexpired, are accepted — the rejection above is about `exp`, nothing else.
    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + live))
        .andExpect(status().isOk());
  }

  @Test
  void listUsers_shouldRejectAnAdminsRefreshTokenUsedAsABearerToken() throws Exception {
    User admin = createUser("authz-refresh@otterworks.dev", User.Role.ADMIN);
    JsonNode tokens = login(admin.getEmail());

    mockMvc
        .perform(
            get("/api/v1/auth/users")
                .header("Authorization", "Bearer " + tokens.get("refreshToken").asText()))
        .andExpect(status().isForbidden());
  }

  @Test
  void listUsers_shouldIgnoreAnAdminRoleSuppliedAsAPlainRequestHeader() throws Exception {
    String userToken = tokenFor(createUser("authz-spoof@otterworks.dev", User.Role.USER));

    mockMvc
        .perform(
            get("/api/v1/auth/users")
                .header("Authorization", "Bearer " + userToken)
                .header("X-User-Roles", "ADMIN")
                .header("X-User-ID", UUID.randomUUID().toString()))
        .andExpect(status().isForbidden());
  }

  // ---------- authenticated-but-not-admin routes ----------

  @Test
  void lookup_shouldBeAllowedForANonAdminButRejectedAnonymously() throws Exception {
    User user = createUser("authz-lookup@otterworks.dev", User.Role.USER);

    mockMvc
        .perform(
            get("/api/v1/auth/users/lookup")
                .header("Authorization", "Bearer " + tokenFor(user))
                .param("email", user.getEmail()))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.email").value(user.getEmail()));

    mockMvc
        .perform(get("/api/v1/auth/users/lookup").param("email", user.getEmail()))
        .andExpect(status().isForbidden());
  }

  /**
   * Observed behaviour, flagged rather than judged: {@code /users/by-id/**} is {@code
   * authenticated()} in {@code SecurityConfig}, so any signed-in user can read any other user's
   * profile. That reads as deliberate (it is the inter-service lookup other OtterWorks services
   * call), so this test pins it instead of asserting a 403.
   */
  @Test
  void lookupById_isReadableByAnyAuthenticatedUser_byDesign() throws Exception {
    User alice = createUser("authz-alice@otterworks.dev", User.Role.USER);
    User bob = createUser("authz-bob@otterworks.dev", User.Role.USER);

    mockMvc
        .perform(
            get("/api/v1/auth/users/by-id/" + bob.getId())
                .header("Authorization", "Bearer " + tokenFor(alice)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.email").value(bob.getEmail()));
  }

  @Test
  void lookupById_shouldRejectAnAnonymousRequest() throws Exception {
    User bob = createUser("authz-anon@otterworks.dev", User.Role.USER);

    mockMvc
        .perform(get("/api/v1/auth/users/by-id/" + bob.getId()))
        .andExpect(status().isForbidden());
  }

  @Test
  void publicRoutes_shouldStayReachableWithoutAToken() throws Exception {
    mockMvc.perform(get("/health")).andExpect(status().isOk());
    mockMvc
        .perform(
            post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"email\": \"nobody@otterworks.dev\", \"password\": \"password123\"}"))
        .andExpect(status().isBadRequest());
  }

  /**
   * DEFECT WP05-6 (judged genuine, not planted; same root cause as WP05-1): authorization is
   * decided purely from the token's {@code roles} claim, with no read-back of the account. An admin
   * whose account has been deleted keeps full admin access until the token expires — up to an hour.
   */
  @Test
  void listUsers_currentlyAcceptsATokenBelongingToADeletedAdmin_seeDefectWp05x6() throws Exception {
    User admin = createUser("authz-deleted@otterworks.dev", User.Role.ADMIN);
    String adminToken = tokenFor(admin);
    refreshTokenRepository.deleteAll();
    userRepository.deleteById(admin.getId());
    assertThat(userRepository.findById(admin.getId())).isEmpty();

    mockMvc
        .perform(get("/api/v1/auth/users").header("Authorization", "Bearer " + adminToken))
        .andExpect(status().isOk());
  }

  // ---------- helpers ----------

  private User createUser(String email, User.Role... roles) {
    User user = new User();
    user.setEmail(email);
    user.setPasswordHash(passwordEncoder.encode("password123"));
    user.setDisplayName("WP05 Authz User");
    user.setRoles(Set.of(roles));
    return userRepository.save(user);
  }

  private JsonNode login(String email) throws Exception {
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/auth/login")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(
                        String.format("{\"email\": \"%s\", \"password\": \"password123\"}", email)))
            .andExpect(status().isOk())
            .andReturn();
    return objectMapper.readTree(result.getResponse().getContentAsString());
  }

  private String tokenFor(User user) throws Exception {
    return login(user.getEmail()).get("accessToken").asText();
  }
}
