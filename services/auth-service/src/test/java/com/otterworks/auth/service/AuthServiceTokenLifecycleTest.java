package com.otterworks.auth.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.otterworks.auth.dto.AuthResponse;
import com.otterworks.auth.dto.LoginRequest;
import com.otterworks.auth.entity.RefreshToken;
import com.otterworks.auth.entity.User;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import com.otterworks.auth.security.JwtTokenProvider;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.security.crypto.password.PasswordEncoder;

/**
 * Refresh-token rotation/replay, logout semantics, and the absence of any failed-login throttle,
 * exercised against mocked collaborators so no wall-clock or database timing is involved.
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AuthServiceTokenLifecycleTest {

  private static final String REFRESH_TOKEN = "refresh-token-1";
  private static final String ROTATED_REFRESH_TOKEN = "refresh-token-2";
  private static final String JTI = "jti-1";
  private static final String ROTATED_JTI = "jti-2";

  @Mock private UserRepository userRepository;
  @Mock private PasswordEncoder passwordEncoder;
  @Mock private JwtTokenProvider jwtTokenProvider;
  @Mock private RefreshTokenRepository refreshTokenRepository;

  private AuthService authService;
  private User user;

  @BeforeEach
  void setUp() {
    authService =
        new AuthService(userRepository, passwordEncoder, jwtTokenProvider, refreshTokenRepository);

    user = new User();
    user.setId(UUID.randomUUID());
    user.setEmail("lifecycle@otterworks.dev");
    user.setDisplayName("Lifecycle User");
    user.setPasswordHash(
        "$2a$12$hashedpassword"); // nosemgrep: generic.secrets.security.detected-bcrypt-hash
    user.setRoles(Set.of(User.Role.USER));

    when(userRepository.findById(user.getId())).thenReturn(Optional.of(user));
    when(userRepository.save(any(User.class))).thenAnswer(inv -> inv.getArgument(0));
    when(refreshTokenRepository.save(any(RefreshToken.class)))
        .thenAnswer(inv -> inv.getArgument(0));
    when(jwtTokenProvider.getAccessTokenExpiry()).thenReturn(3600L);
    when(jwtTokenProvider.getRefreshTokenExpiry()).thenReturn(2592000L);
    when(jwtTokenProvider.generateAccessToken(any(User.class))).thenReturn("access-token");
    when(jwtTokenProvider.generateRefreshToken(any(User.class))).thenReturn(ROTATED_REFRESH_TOKEN);
    when(jwtTokenProvider.extractJti(REFRESH_TOKEN)).thenReturn(JTI);
    when(jwtTokenProvider.extractJti(ROTATED_REFRESH_TOKEN)).thenReturn(ROTATED_JTI);
    when(jwtTokenProvider.validateRefreshTokenAndGetUserId(REFRESH_TOKEN))
        .thenReturn(user.getId().toString());
  }

  // ---------------------------------------------------------------- rotation & replay

  @Test
  void refresh_rotatesTheTokenAndRevokesItsPredecessor() {
    RefreshToken stored = storedToken(JTI, Instant.now().plusSeconds(3600));
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI)).thenReturn(Optional.of(stored));

    AuthResponse response = authService.refreshToken(REFRESH_TOKEN);

    assertThat(stored.isRevoked()).isTrue();
    assertThat(response.getRefreshToken()).isEqualTo(ROTATED_REFRESH_TOKEN);

    ArgumentCaptor<RefreshToken> saved = ArgumentCaptor.forClass(RefreshToken.class);
    verify(refreshTokenRepository, times(2)).save(saved.capture());
    List<String> persistedJtis = new ArrayList<>();
    saved.getAllValues().forEach(t -> persistedJtis.add(t.getTokenId()));
    assertThat(persistedJtis).containsExactly(JTI, ROTATED_JTI);
  }

  @Test
  void refresh_replayOfAnAlreadyRotatedTokenIsRejected() {
    RefreshToken stored = storedToken(JTI, Instant.now().plusSeconds(3600));
    // First lookup finds the live row; after rotation the same jti is revoked, so the
    // revoked-false lookup comes back empty.
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI))
        .thenReturn(Optional.of(stored))
        .thenReturn(Optional.empty());

    authService.refreshToken(REFRESH_TOKEN);

    assertThatThrownBy(() -> authService.refreshToken(REFRESH_TOKEN))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Invalid or revoked refresh token");
  }

  @Test
  void refresh_replayIssuesNoNewTokensOnTheSecondAttempt() {
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI)).thenReturn(Optional.empty());

    assertThatThrownBy(() -> authService.refreshToken(REFRESH_TOKEN))
        .isInstanceOf(IllegalArgumentException.class);

    verify(jwtTokenProvider, never()).generateAccessToken(any(User.class));
    verify(refreshTokenRepository, never()).save(any(RefreshToken.class));
  }

  @Test
  void refresh_withAnUnknownJtiIsRejected() {
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI)).thenReturn(Optional.empty());

    assertThatThrownBy(() -> authService.refreshToken(REFRESH_TOKEN))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Invalid or revoked refresh token");
  }

  @Test
  void refresh_whenTheOwningUserNoLongerExistsIsRejected() {
    RefreshToken stored = storedToken(JTI, Instant.now().plusSeconds(3600));
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI)).thenReturn(Optional.of(stored));
    when(userRepository.findById(user.getId())).thenReturn(Optional.empty());

    assertThatThrownBy(() -> authService.refreshToken(REFRESH_TOKEN))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("User not found");

    // The predecessor is still burned even though the exchange failed.
    assertThat(stored.isRevoked()).isTrue();
  }

  @Test
  void refresh_withATokenThatFailsJwtValidationIsRejected() {
    when(jwtTokenProvider.extractJti("garbage")).thenThrow(new IllegalArgumentException("bad"));

    assertThatThrownBy(() -> authService.refreshToken("garbage"))
        .isInstanceOf(IllegalArgumentException.class);

    verifyNoInteractions(refreshTokenRepository);
  }

  // ---------------------------------------------------------------- stored-row expiry trio

  @Test
  void refresh_isRejectedOneSecondAfterTheStoredRowExpires() {
    RefreshToken stored = storedToken(JTI, Instant.now().minusSeconds(1));
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI)).thenReturn(Optional.of(stored));

    assertThatThrownBy(() -> authService.refreshToken(REFRESH_TOKEN))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessage("Refresh token expired");
  }

  @Test
  void refresh_isAcceptedWhileTheStoredRowIsStillLive() {
    // The "exactly at expiresAt" case cannot be pinned deterministically: AuthService compares
    // against Instant.now() with no injectable Clock (same finding as JwtTokenLifecycleTest), so
    // the live side of the boundary is asserted with a margin rather than a racy +1s.
    RefreshToken stored = storedToken(JTI, Instant.now().plusSeconds(30));
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI)).thenReturn(Optional.of(stored));

    assertThatCode(() -> authService.refreshToken(REFRESH_TOKEN)).doesNotThrowAnyException();
  }

  @Test
  void refresh_ofAnExpiredRowDoesNotRevokeIt() {
    // FINDING (pinned, not fixed): an expired row is left revoked = false, so nothing prunes
    // it and a replayed expired token keeps re-entering the same code path.
    RefreshToken stored = storedToken(JTI, Instant.now().minusSeconds(3600));
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI)).thenReturn(Optional.of(stored));

    assertThatThrownBy(() -> authService.refreshToken(REFRESH_TOKEN))
        .isInstanceOf(IllegalArgumentException.class);

    assertThat(stored.isRevoked()).isFalse();
  }

  @Test
  void refresh_persistsTheRotatedRowWithTheConfiguredTtl() {
    RefreshToken stored = storedToken(JTI, Instant.now().plusSeconds(3600));
    when(refreshTokenRepository.findByTokenIdAndRevokedFalse(JTI)).thenReturn(Optional.of(stored));

    authService.refreshToken(REFRESH_TOKEN);

    ArgumentCaptor<RefreshToken> saved = ArgumentCaptor.forClass(RefreshToken.class);
    verify(refreshTokenRepository, times(2)).save(saved.capture());
    RefreshToken rotated = saved.getAllValues().get(1);
    assertThat(rotated.getTokenId()).isEqualTo(ROTATED_JTI);
    assertThat(rotated.isRevoked()).isFalse();
    assertThat(rotated.getExpiresAt()).isAfter(Instant.now().plusSeconds(2591000));
  }

  // ---------------------------------------------------------------- logout

  @Test
  void logout_revokesEveryRefreshTokenForTheUser() {
    authService.logout(user.getId());

    verify(refreshTokenRepository).revokeAllByUserId(user.getId());
  }

  @Test
  void logout_isIdempotent() {
    authService.logout(user.getId());
    authService.logout(user.getId());

    verify(refreshTokenRepository, times(2)).revokeAllByUserId(user.getId());
  }

  @Test
  void logout_doesNotTouchTheAccessTokenAtAll() {
    // FINDING (pinned, not fixed): logout never consults the JWT layer, so an access token
    // that is already in flight stays usable until it expires (up to the full 1h TTL). There
    // is no deny list and access tokens carry no jti to put on one.
    authService.logout(user.getId());

    verifyNoInteractions(jwtTokenProvider);
  }

  @Test
  void logout_forAnUnknownUserIsSilentlyAccepted() {
    // FINDING (pinned, not fixed): logout does not verify the subject exists, so it is a
    // no-op success for any UUID. Harmless today, but it means a caller cannot distinguish
    // "logged out" from "nothing to log out".
    UUID unknown = UUID.randomUUID();

    assertThatCode(() -> authService.logout(unknown)).doesNotThrowAnyException();

    verify(refreshTokenRepository).revokeAllByUserId(unknown);
  }

  // ---------------------------------------------------------------- lockout / throttling

  @ParameterizedTest(name = "{0} consecutive failed logins still leave the account usable")
  @ValueSource(ints = {2, 3, 4, 5, 6, 10})
  void login_isNeverThrottledOrLockedOutAfterRepeatedFailures(int failedAttempts) {
    // FINDING (pinned, not fixed): auth-service implements no account lockout, no failed
    // attempt counter and no throttling. There is no N to probe at N-1 / N / N+1, so this
    // pins the current behaviour: the account remains usable after any number of failures,
    // and nothing about the user record changes. The desired behaviour is asserted by the
    // disabled companion test below.
    LoginRequest wrong = loginRequest("wrong-password");
    when(userRepository.findByEmail(user.getEmail())).thenReturn(Optional.of(user));
    when(passwordEncoder.matches("wrong-password", user.getPasswordHash())).thenReturn(false);
    when(passwordEncoder.matches("correct-password", user.getPasswordHash())).thenReturn(true);

    for (int i = 0; i < failedAttempts; i++) {
      assertThatThrownBy(() -> authService.login(wrong))
          .isInstanceOf(IllegalArgumentException.class)
          .hasMessage("Invalid credentials");
    }

    AuthResponse response = authService.login(loginRequest("correct-password"));

    assertThat(response.getAccessToken()).isEqualTo("access-token");
  }

  @Test
  @Disabled("FINDING: no account lockout exists in auth-service; enable once one is implemented")
  void login_shouldLockTheAccountAfterFiveConsecutiveFailures() {
    LoginRequest wrong = loginRequest("wrong-password");
    when(userRepository.findByEmail(user.getEmail())).thenReturn(Optional.of(user));
    when(passwordEncoder.matches("wrong-password", user.getPasswordHash())).thenReturn(false);
    when(passwordEncoder.matches("correct-password", user.getPasswordHash())).thenReturn(true);

    for (int i = 0; i < 5; i++) {
      assertThatThrownBy(() -> authService.login(wrong))
          .isInstanceOf(IllegalArgumentException.class);
    }

    // Desired behaviour: the 6th attempt is refused even with the correct password.
    assertThatThrownBy(() -> authService.login(loginRequest("correct-password")))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessageContaining("locked");
  }

  @Test
  void login_failuresDoNotPersistAnythingOnTheUserRecord() {
    when(userRepository.findByEmail(user.getEmail())).thenReturn(Optional.of(user));
    when(passwordEncoder.matches("wrong-password", user.getPasswordHash())).thenReturn(false);

    for (int i = 0; i < 5; i++) {
      assertThatThrownBy(() -> authService.login(loginRequest("wrong-password")))
          .isInstanceOf(IllegalArgumentException.class);
    }

    verify(userRepository, never()).save(any(User.class));
  }

  @Test
  void login_failureForAnUnknownEmailIsIndistinguishableFromAWrongPassword() {
    when(userRepository.findByEmail("ghost@otterworks.dev")).thenReturn(Optional.empty());
    when(userRepository.findByEmail(user.getEmail())).thenReturn(Optional.of(user));
    when(passwordEncoder.matches("wrong-password", user.getPasswordHash())).thenReturn(false);

    LoginRequest unknownEmail = new LoginRequest();
    unknownEmail.setEmail("ghost@otterworks.dev");
    unknownEmail.setPassword("wrong-password");

    assertThatThrownBy(() -> authService.login(unknownEmail)).hasMessage("Invalid credentials");
    assertThatThrownBy(() -> authService.login(loginRequest("wrong-password")))
        .hasMessage("Invalid credentials");
  }

  @Test
  void login_doesNotRevokeExistingRefreshTokens() {
    // Pins today's session model: logging in again adds a refresh token rather than
    // replacing the previous session's.
    when(userRepository.findByEmail(user.getEmail())).thenReturn(Optional.of(user));
    when(passwordEncoder.matches("correct-password", user.getPasswordHash())).thenReturn(true);

    authService.login(loginRequest("correct-password"));
    authService.login(loginRequest("correct-password"));

    verify(refreshTokenRepository, never()).revokeAllByUserId(any(UUID.class));
    verify(refreshTokenRepository, times(2)).save(any(RefreshToken.class));
  }

  // ---------------------------------------------------------------- helpers

  private LoginRequest loginRequest(String password) {
    LoginRequest request = new LoginRequest();
    request.setEmail(user.getEmail());
    request.setPassword(password);
    return request;
  }

  private RefreshToken storedToken(String jti, Instant expiresAt) {
    RefreshToken token = new RefreshToken();
    token.setId(UUID.randomUUID());
    token.setUser(user);
    token.setTokenId(jti);
    token.setExpiresAt(expiresAt);
    token.setRevoked(false);
    return token;
  }
}
