package com.otterworks.auth.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.otterworks.auth.entity.User;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import io.jsonwebtoken.JwtBuilder;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.function.UnaryOperator;
import javax.crypto.SecretKey;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders;

/**
 * Authorization negatives for the ADMIN-only route and for cross-user access.
 *
 * <p>The secret matches {@code src/test/resources/application-test.yml}, which lets these tests
 * mint tokens the running service considers authentic - the point being to prove what the service
 * derives from a token's claims rather than from the user record behind it.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AdminRouteAuthorizationTest {

  private static final String TEST_SECRET =
      "test-jwt-secret-otterworks-must-be-at-least-32-bytes-long-for-hmac"; // nosemgrep:
  // generic.secrets.security.detected-generic-secret
  private static final String FOREIGN_SECRET =
      "forged-jwt-secret-otterworks-also-at-least-32-bytes-long-for-hmac"; // nosemgrep:
  // generic.secrets.security.detected-generic-secret
  private static final String ADMIN_ROUTE = "/api/v1/auth/users";

  private final SecretKey key = Keys.hmacShaKeyFor(TEST_SECRET.getBytes(StandardCharsets.UTF_8));
  private final SecretKey foreignKey =
      Keys.hmacShaKeyFor(FOREIGN_SECRET.getBytes(StandardCharsets.UTF_8));

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private UserRepository userRepository;
  @Autowired private RefreshTokenRepository refreshTokenRepository;
  @Autowired private PasswordEncoder passwordEncoder;

  @BeforeEach
  void resetDatabase() {
    refreshTokenRepository.deleteAll();
    userRepository.deleteAll();
  }

  // ---------------------------------------------------------------- admin route negatives

  @Test
  void adminRoute_rejectsAnUnauthenticatedRequest() throws Exception {
    mockMvc.perform(get(ADMIN_ROUTE)).andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsATokenWithTheUserRole() throws Exception {
    String token =
        tokenFor(createUser("plainuser@otterworks.dev", User.Role.USER), List.of("USER"));

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsATokenWithNoRolesClaim() throws Exception {
    User user = createUser("noroles@otterworks.dev", User.Role.USER);
    String token =
        signedWith(
            key, builder -> builder.subject(user.getId().toString()).claim("type", "access"));

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsATokenWithAnEmptyRolesClaim() throws Exception {
    User user = createUser("emptyroles@otterworks.dev", User.Role.USER);
    String token = tokenFor(user, List.of());

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsATokenClaimingAdminButSignedWithAForeignKey() throws Exception {
    User user = createUser("foreignkey@otterworks.dev", User.Role.USER);
    String token =
        signedWith(
            foreignKey,
            builder ->
                builder
                    .subject(user.getId().toString())
                    .claim("type", "access")
                    .claim("roles", List.of("ADMIN")));

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsAnUnsignedAlgNoneTokenClaimingAdmin() throws Exception {
    User user = createUser("algnone@otterworks.dev", User.Role.USER);
    String token =
        Jwts.builder()
            .subject(user.getId().toString())
            .claim("type", "access")
            .claim("roles", List.of("ADMIN"))
            .issuedAt(Date.from(Instant.now()))
            .expiration(Date.from(Instant.now().plusSeconds(3600)))
            .compact();

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsAnExpiredAdminToken() throws Exception {
    User admin = createUser("expiredadmin@otterworks.dev", User.Role.ADMIN, User.Role.USER);
    String token =
        Jwts.builder()
            .subject(admin.getId().toString())
            .claim("type", "access")
            .claim("roles", List.of("ADMIN", "USER"))
            .issuedAt(Date.from(Instant.now().minusSeconds(7200)))
            .expiration(Date.from(Instant.now().minusSeconds(1)))
            .signWith(key)
            .compact();

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsARefreshTokenPresentedAsABearerToken() throws Exception {
    User admin = createUser("refreshadmin@otterworks.dev", User.Role.ADMIN, User.Role.USER);
    String refreshLikeToken =
        signedWith(
            key,
            builder ->
                builder
                    .subject(admin.getId().toString())
                    .id(UUID.randomUUID().toString())
                    .claim("type", "refresh")
                    .claim("roles", List.of("ADMIN")));

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + refreshLikeToken))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsAMalformedAuthorizationHeader() throws Exception {
    String token =
        tokenFor(createUser("malformed@otterworks.dev", User.Role.ADMIN), List.of("ADMIN"));

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", token))
        .andExpect(status().isForbidden());
    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Basic " + token))
        .andExpect(status().isForbidden());
    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_rejectsAnUnknownRoleName() throws Exception {
    User user = createUser("superadmin@otterworks.dev", User.Role.USER);
    String token = tokenFor(user, List.of("SUPERADMIN", "ROOT"));

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isForbidden());
  }

  @Test
  void adminRoute_acceptsAGenuineAdminToken() throws Exception {
    User admin = createUser("realadmin@otterworks.dev", User.Role.ADMIN, User.Role.USER);
    String token = tokenFor(admin, List.of("ADMIN", "USER"));

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.content").isArray());
  }

  @Test
  void adminRoute_authorizesFromTheRolesClaimWithoutRereadingTheUserRecord() throws Exception {
    // FINDING (pinned, not fixed): the roles claim is the sole source of authority. A token
    // that says ADMIN is honoured even though the user row it points at only holds USER, so a
    // role revoked in the database stays effective for the token's whole 1h lifetime (and any
    // holder of the signing secret can self-issue admin).
    User user = createUser("claimonly@otterworks.dev", User.Role.USER);
    String token = tokenFor(user, List.of("ADMIN"));

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isOk());
  }

  @Test
  void adminRoute_honoursATokenWhoseSubjectHasBeenDeleted() throws Exception {
    // FINDING (pinned, not fixed): nothing checks that the subject still exists, so a token
    // for a deleted account still lists every user.
    User user = createUser("deleted@otterworks.dev", User.Role.ADMIN, User.Role.USER);
    String token = tokenFor(user, List.of("ADMIN"));
    userRepository.deleteAll();

    mockMvc
        .perform(get(ADMIN_ROUTE).header("Authorization", "Bearer " + token))
        .andExpect(status().isOk());
  }

  // ---------------------------------------------------------------- subject handling

  @Test
  void tokenWithoutSubject_currentlyCausesAServerError() throws Exception {
    // FINDING (pinned, not fixed): a signed token with no `sub` authenticates with a null
    // principal, and AuthController then calls UUID.fromString(null) -> 500 rather than 401.
    String token =
        signedWith(key, builder -> builder.claim("type", "access").claim("roles", List.of("USER")));

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + token))
        .andExpect(status().is5xxServerError());
  }

  @Test
  void tokenWithANonUuidSubject_isRejectedWithABadRequest() throws Exception {
    // Contrast with the null-subject case above: a non-UUID subject raises
    // IllegalArgumentException, which the handler does map to 400.
    String token =
        signedWith(
            key,
            builder ->
                builder
                    .subject("not-a-uuid")
                    .claim("type", "access")
                    .claim("roles", List.of("USER")));

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + token))
        .andExpect(status().isBadRequest());
  }

  @Test
  void tokenForAnUnknownButWellFormedSubject_isRejectedWithABadRequest() throws Exception {
    String token =
        signedWith(
            key,
            builder ->
                builder
                    .subject(UUID.randomUUID().toString())
                    .claim("type", "access")
                    .claim("roles", List.of("USER")));

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + token))
        .andExpect(status().isBadRequest());
  }

  // ---------------------------------------------------------------- cross-user access

  @Test
  void profile_alwaysResolvesToTheTokenSubjectAndNeverToAnotherUser() throws Exception {
    User alice = createUser("alice@otterworks.dev", User.Role.USER);
    createUser("bob@otterworks.dev", User.Role.USER);
    String aliceToken = tokenFor(alice, List.of("USER"));

    mockMvc
        .perform(get("/api/v1/auth/profile").header("Authorization", "Bearer " + aliceToken))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.email").value("alice@otterworks.dev"));
  }

  @Test
  void updateProfile_cannotBeAimedAtAnotherUsersRecord() throws Exception {
    User alice = createUser("alice-update@otterworks.dev", User.Role.USER);
    User bob = createUser("bob-update@otterworks.dev", User.Role.USER);
    String aliceToken = tokenFor(alice, List.of("USER"));

    // The route takes no id, so a caller cannot address Bob at all: an id smuggled into the
    // body is ignored and Alice's own record is what changes.
    mockMvc
        .perform(
            MockMvcRequestBuilders.put("/api/v1/auth/profile")
                .header("Authorization", "Bearer " + aliceToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    String.format("{\"displayName\": \"Renamed\", \"id\": \"%s\"}", bob.getId())))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.email").value("alice-update@otterworks.dev"));

    MvcResult bobAfter =
        mockMvc
            .perform(
                get("/api/v1/auth/users/by-id/" + bob.getId())
                    .header("Authorization", "Bearer " + aliceToken))
            .andExpect(status().isOk())
            .andReturn();
    JsonNode bobJson = objectMapper.readTree(bobAfter.getResponse().getContentAsString());
    assertThat(bobJson.get("displayName").asText()).isNotEqualTo("Renamed");
  }

  @Test
  void userLookup_isOpenToAnyAuthenticatedUserByDesign() throws Exception {
    // Pinned rather than flagged: SecurityConfig explicitly maps /users/lookup and
    // /users/by-id/** to .authenticated() so sibling services can resolve collaborators.
    // The assertion exists so that narrowing it later is a deliberate, visible change.
    User alice = createUser("alice-lookup@otterworks.dev", User.Role.USER);
    User bob = createUser("bob-lookup@otterworks.dev", User.Role.USER);
    String aliceToken = tokenFor(alice, List.of("USER"));

    mockMvc
        .perform(
            get("/api/v1/auth/users/lookup")
                .param("email", bob.getEmail())
                .header("Authorization", "Bearer " + aliceToken))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.email").value("bob-lookup@otterworks.dev"));
    mockMvc
        .perform(
            get("/api/v1/auth/users/by-id/" + bob.getId())
                .header("Authorization", "Bearer " + aliceToken))
        .andExpect(status().isOk());
  }

  @Test
  void userLookup_requiresAuthentication() throws Exception {
    User bob = createUser("bob-anon@otterworks.dev", User.Role.USER);

    mockMvc
        .perform(get("/api/v1/auth/users/lookup").param("email", bob.getEmail()))
        .andExpect(status().isForbidden());
    mockMvc
        .perform(get("/api/v1/auth/users/by-id/" + bob.getId()))
        .andExpect(status().isForbidden());
  }

  @Test
  void userLookup_ofAnUnknownEmailIsARejection() throws Exception {
    User alice = createUser("alice-missing@otterworks.dev", User.Role.USER);
    String aliceToken = tokenFor(alice, List.of("USER"));

    mockMvc
        .perform(
            get("/api/v1/auth/users/lookup")
                .param("email", "ghost@otterworks.dev")
                .header("Authorization", "Bearer " + aliceToken))
        .andExpect(status().isBadRequest());
  }

  // ---------------------------------------------------------------- helpers

  private User createUser(String email, User.Role... roles) {
    User user = new User();
    user.setEmail(email);
    user.setPasswordHash(passwordEncoder.encode("password123"));
    user.setDisplayName("User " + email);
    user.setRoles(Set.of(roles));
    return userRepository.save(user);
  }

  private String tokenFor(User user, List<String> roles) {
    return signedWith(
        key,
        builder ->
            builder
                .subject(user.getId().toString())
                .claim("email", user.getEmail())
                .claim("type", "access")
                .claim("roles", roles));
  }

  private String signedWith(SecretKey signingKey, UnaryOperator<JwtBuilder> customizer) {
    Instant now = Instant.now();
    return customizer
        .apply(Jwts.builder())
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(3600)))
        .signWith(signingKey)
        .compact();
  }
}
