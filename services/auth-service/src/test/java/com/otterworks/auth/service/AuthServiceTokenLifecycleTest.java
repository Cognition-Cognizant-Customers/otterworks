package com.otterworks.auth.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

import com.otterworks.auth.dto.AuthResponse;
import com.otterworks.auth.entity.RefreshToken;
import com.otterworks.auth.entity.User;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import com.otterworks.auth.security.JwtTokenProvider;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.crypto.password.PasswordEncoder;

/**
 * Refresh-token rotation / replay and logout semantics at the service layer (WP-05).
 *
 * <p>Every case builds its own fixtures; nothing is shared or mutated across tests.
 */
@ExtendWith(MockitoExtension.class)
class AuthServiceTokenLifecycleTest {

  @Mock private UserRepository userRepository;
  @Mock private PasswordEncoder passwordEncoder;
  @Mock private JwtTokenProvider jwtTokenProvider;
  @Mock private RefreshTokenRepository refreshTokenRepository;

  @InjectMocks private AuthService authService;

  private User user;

  @BeforeEach
  void setUp() {
    user = new User();
    user.setId(UUID.randomUUID());
    user.setEmail("lifecycle@otterworks.dev");
    user.setDisplayName("Lifecycle User");
    user.setPasswordHash(
        "$2a$12$encoded"); // nosemgrep: generic.secrets.security.detected-bcrypt-hash
    user.setRoles(Set.of(User.Role.USER));
  }

  // ---------- positive: rotation ----------

  @Test
  void refresh_shouldRevokePresentedTokenAndIssueANewOne() {
    RefreshToken stored = storedToken("jti-old", Instant.now().plus(30, ChronoUnit.DAYS), false);
    stubRefreshFlow("old-refresh", "jti-old", stored);
    stubTokenIssuance("new-access", "new-refresh", "jti-new");

    AuthResponse response = authService.refreshToken("old-refresh");

    assertThat(response.getAccessToken()).isEqualTo("new-access");
    assertThat(response.getRefreshToken()).isEqualTo("new-refresh").isNotEqualTo("old-refresh");
    assertThat(stored.isRevoked()).as("presented refresh token must be rotated out").isTrue();

    ArgumentCaptor<RefreshToken> saved = ArgumentCaptor.forClass(RefreshToken.class);
    verify(refreshTokenRepository, times(2)).save(saved.capture());
    assertThat(saved.getAllValues().get(0).getTokenId()).isEqualTo("jti-old");
    assertThat(saved.getAllValues().get(0).isRevoked()).isTrue();
    assertThat(saved.getAllValues().get(1).getTokenId()).isEqualTo("jti-new");
    assertThat(saved.getAllValues().get(1).isRevoked()).isFalse();
  }

  @Test
  void refresh_shouldPersistTheNewTokenWithTheProvidersRefreshExpiry() {
    RefreshToken stored = storedToken("jti-old", Instant.now().plus(30, ChronoUnit.DAYS), false);
    stubRefreshFlow("old-refresh", "jti-old", stored);
    stubTokenIssuance("new-access", "new-refresh", "jti-new");
    Instant before = Instant.now();

    authService.refreshToken("old-refresh");

    ArgumentCaptor<RefreshToken> saved = ArgumentCaptor.forClass(RefreshToken.class);
    verify(refreshTokenRepository, times(2)).save(saved.capture());
    RefreshToken issued = saved.getAllValues().get(1);
    assertThat(issued.getExpiresAt())
        .isBetween(before.plusSeconds(2592000), Instant.now().plusSeconds(2592000));
    assertThat(issued.getUser()).isSameAs(user);
  }

  // ---------- negative: replay ----------

  @Test
  void refresh_shouldRejectAReplayedTokenBecauseItIsNoLongerUnrevoked() {
    when(jwtTokenProvider.extractJti("used-refresh")).thenReturn("jti-used");
    when(jwtTokenProvider.validateRefreshTokenAndGetUserId("used-refresh"))
        .thenReturn(user.getId().toString());
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse("jti-used"))
        .thenReturn(Optional.empty());

    assertThatThrownBy(() -> authService.refreshToken("used-refresh"))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Invalid or revoked refresh token");

    verify(refreshTokenRepository, never()).save(any(RefreshToken.class));
    verify(jwtTokenProvider, never()).generateAccessToken(any(User.class));
  }

  @Test
  void refresh_shouldRejectATokenRevokedByLogout() {
    when(jwtTokenProvider.extractJti("logged-out")).thenReturn("jti-logged-out");
    when(jwtTokenProvider.validateRefreshTokenAndGetUserId("logged-out"))
        .thenReturn(user.getId().toString());
    // revokeAllByUserId flipped `revoked`, so the "and revoked false" lookup finds nothing.
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse("jti-logged-out"))
        .thenReturn(Optional.empty());

    assertThatThrownBy(() -> authService.refreshToken("logged-out"))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Invalid or revoked refresh token");
  }

  @Test
  void refresh_shouldRejectAnAccessTokenPresentedAsARefreshToken() {
    when(jwtTokenProvider.extractJti("access-token")).thenReturn(null);
    when(jwtTokenProvider.validateRefreshTokenAndGetUserId("access-token"))
        .thenThrow(new IllegalArgumentException("Token is not a refresh token"));

    assertThatThrownBy(() -> authService.refreshToken("access-token"))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Token is not a refresh token");

    verifyNoInteractions(refreshTokenRepository);
  }

  @Test
  void refresh_shouldRejectWhenTheOwningUserNoLongerExists() {
    RefreshToken stored = storedToken("jti-orphan", Instant.now().plus(1, ChronoUnit.DAYS), false);
    stubRefreshFlow("orphan-refresh", "jti-orphan", stored);
    when(userRepository.findById(user.getId())).thenReturn(Optional.empty());

    assertThatThrownBy(() -> authService.refreshToken("orphan-refresh"))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("User not found");
  }

  // ---------- boundary: stored-token expiry ----------
  //
  // AuthService compares expiresAt against Instant.now() at call time and takes no injectable
  // clock, so neither `expiresAt == now` nor a sub-second cushion can be asserted without a
  // race — a GC pause between building the fixture and the comparison would flip the result.
  // The trio is therefore "comfortably ahead" / "just elapsed" / "long elapsed"; the exact
  // instant is covered deterministically against a fixed clock in JwtClockSkewTest.

  @Test
  void refresh_shouldAcceptATokenThatHasNotYetExpired() {
    RefreshToken stored = storedToken("jti-edge", Instant.now().plusSeconds(60), false);
    stubRefreshFlow("edge-refresh", "jti-edge", stored);
    stubTokenIssuance("new-access", "new-refresh", "jti-new");

    assertThat(authService.refreshToken("edge-refresh").getAccessToken()).isEqualTo("new-access");
  }

  @Test
  void refresh_shouldRejectATokenThatExpiredOneSecondAgo() {
    RefreshToken stored = storedToken("jti-edge", Instant.now().minusSeconds(1), false);
    stubRefreshFlow("edge-refresh", "jti-edge", stored);

    assertThatThrownBy(() -> authService.refreshToken("edge-refresh"))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Refresh token expired");
    assertThat(stored.isRevoked()).as("an expired token is not rotated").isFalse();
  }

  @Test
  void refresh_shouldRejectALongExpiredToken() {
    RefreshToken stored = storedToken("jti-old", Instant.now().minus(60, ChronoUnit.DAYS), false);
    stubRefreshFlow("stale-refresh", "jti-old", stored);

    assertThatThrownBy(() -> authService.refreshToken("stale-refresh"))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Refresh token expired");
  }

  // ---------- logout ----------

  @Test
  void logout_shouldRevokeEveryRefreshTokenOwnedByTheUser() {
    authService.logout(user.getId());

    verify(refreshTokenRepository).revokeAllByUserId(user.getId());
    verifyNoMoreInteractions(refreshTokenRepository);
    verifyNoInteractions(jwtTokenProvider);
  }

  @Test
  void logout_shouldBeIdempotent() {
    authService.logout(user.getId());
    authService.logout(user.getId());

    verify(refreshTokenRepository, times(2)).revokeAllByUserId(user.getId());
  }

  @Test
  void logout_shouldNotTouchAnotherUsersTokens() {
    UUID otherUser = UUID.randomUUID();

    authService.logout(user.getId());

    verify(refreshTokenRepository, never()).revokeAllByUserId(otherUser);
  }

  @Test
  void logout_shouldSucceedForAUserThatHasNoStoredTokens() {
    UUID neverLoggedIn = UUID.randomUUID();

    assertThatCode(() -> authService.logout(neverLoggedIn)).doesNotThrowAnyException();

    verify(refreshTokenRepository).revokeAllByUserId(neverLoggedIn);
    verifyNoInteractions(userRepository);
  }

  // ---------- password-policy note ----------
  //
  // The service layer applies no password policy at all: @Size(min = 8, max = 128) lives on
  // the request DTOs and is enforced by @Valid at the controller. These two cases pin that
  // split so a future move of the rule into AuthService is a deliberate, visible change.

  @ParameterizedTest
  @ValueSource(strings = {"a", "1234567", "\u00e9"})
  void changePassword_serviceLayerAcceptsPasswordsThatTheDtoWouldReject(String shortPassword) {
    com.otterworks.auth.dto.ChangePasswordRequest request =
        new com.otterworks.auth.dto.ChangePasswordRequest();
    request.setCurrentPassword("password123");
    request.setNewPassword(shortPassword);
    when(userRepository.findById(user.getId())).thenReturn(Optional.of(user));
    when(passwordEncoder.matches("password123", user.getPasswordHash())).thenReturn(true);
    when(passwordEncoder.encode(shortPassword)).thenReturn("$2a$12$new");

    authService.changePassword(user.getId(), request);

    verify(passwordEncoder).encode(shortPassword);
    verify(refreshTokenRepository).revokeAllByUserId(user.getId());
  }

  // ---------- helpers ----------

  private RefreshToken storedToken(String jti, Instant expiresAt, boolean revoked) {
    RefreshToken token = new RefreshToken();
    token.setUser(user);
    token.setTokenId(jti);
    token.setExpiresAt(expiresAt);
    token.setRevoked(revoked);
    return token;
  }

  private void stubRefreshFlow(String presented, String jti, RefreshToken stored) {
    when(jwtTokenProvider.extractJti(presented)).thenReturn(jti);
    when(jwtTokenProvider.validateRefreshTokenAndGetUserId(presented))
        .thenReturn(user.getId().toString());
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(jti)).thenReturn(Optional.of(stored));
  }

  private void stubTokenIssuance(String accessToken, String refreshToken, String newJti) {
    when(userRepository.findById(user.getId())).thenReturn(Optional.of(user));
    when(jwtTokenProvider.generateAccessToken(user)).thenReturn(accessToken);
    when(jwtTokenProvider.generateRefreshToken(user)).thenReturn(refreshToken);
    when(jwtTokenProvider.extractJti(refreshToken)).thenReturn(newJti);
    when(jwtTokenProvider.getAccessTokenExpiry()).thenReturn(3600L);
    when(jwtTokenProvider.getRefreshTokenExpiry()).thenReturn(2592000L);
    when(refreshTokenRepository.save(any(RefreshToken.class)))
        .thenAnswer(inv -> inv.getArgument(0));
  }
}
