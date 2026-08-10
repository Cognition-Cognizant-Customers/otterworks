package com.otterworks.auth.service;

import com.otterworks.auth.dto.AuthResponse;
import com.otterworks.auth.dto.ChangePasswordRequest;
import com.otterworks.auth.dto.LoginRequest;
import com.otterworks.auth.dto.RegisterRequest;
import com.otterworks.auth.entity.RefreshToken;
import com.otterworks.auth.entity.User;
import com.otterworks.auth.exception.AccountLockedException;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import com.otterworks.auth.security.JwtTokenProvider;
import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AuthService {

  private static final Logger log = LoggerFactory.getLogger(AuthService.class);

  /** Failed attempts on one account before it is temporarily locked. */
  static final int LOCKOUT_THRESHOLD = 5;

  /** Lock duration for a first lockout; doubles with each consecutive one. */
  static final Duration LOCKOUT_BASE = Duration.ofSeconds(30);

  /** Cap on exponential backoff doublings (30s * 2^5 = 16 minutes max). */
  private static final int MAX_LOCKOUT_DOUBLINGS = 5;

  private final UserRepository userRepository;
  private final PasswordEncoder passwordEncoder;
  private final JwtTokenProvider jwtTokenProvider;
  private final RefreshTokenRepository refreshTokenRepository;

  public AuthService(
      UserRepository userRepository,
      PasswordEncoder passwordEncoder,
      JwtTokenProvider jwtTokenProvider,
      RefreshTokenRepository refreshTokenRepository) {
    this.userRepository = userRepository;
    this.passwordEncoder = passwordEncoder;
    this.jwtTokenProvider = jwtTokenProvider;
    this.refreshTokenRepository = refreshTokenRepository;
  }

  @Transactional
  public AuthResponse register(RegisterRequest request) {
    if (userRepository.existsByEmail(request.getEmail())) {
      throw new IllegalArgumentException("Email already registered");
    }

    User user = new User();
    user.setEmail(request.getEmail());
    user.setPasswordHash(passwordEncoder.encode(request.getPassword()));
    user.setDisplayName(request.getDisplayName());
    user.setRoles(Set.of(User.Role.USER));
    user = userRepository.save(user);

    log.info("User registered: email={}", user.getEmail());
    return buildAuthResponse(user);
  }

  @Transactional(noRollbackFor = {IllegalArgumentException.class, AccountLockedException.class})
  public AuthResponse login(LoginRequest request) {
    User user =
        userRepository
            .findByEmailForUpdate(request.getEmail())
            .orElseThrow(() -> new IllegalArgumentException("Invalid credentials"));

    Instant now = Instant.now();
    if (user.getLockedUntil() != null) {
      if (now.isBefore(user.getLockedUntil())) {
        // Refused without touching the lockout state: attempts made while locked must not be
        // able to push the window forward, or anyone could keep an account locked forever.
        throw new AccountLockedException(
            "Account temporarily locked due to repeated failed login attempts");
      }
      user.setLockedUntil(null);
      user.setFailedLoginAttempts(0);
    }

    if (!passwordEncoder.matches(request.getPassword(), user.getPasswordHash())) {
      recordFailedAttempt(user, now);
      throw new IllegalArgumentException("Invalid credentials");
    }

    user.setFailedLoginAttempts(0);
    user.setLockedUntil(null);
    user.setLockoutCycles(0);
    user.setLastLoginAt(now);
    userRepository.save(user);

    log.info("User logged in: email={}", user.getEmail());
    return buildAuthResponse(user);
  }

  @Transactional
  public AuthResponse refreshToken(String token) {
    String jti = jwtTokenProvider.extractJti(token);
    String userId = jwtTokenProvider.validateRefreshTokenAndGetUserId(token);

    RefreshToken storedToken =
        refreshTokenRepository
            .findByTokenIdAndRevokedFalse(jti)
            .orElseThrow(() -> new IllegalArgumentException("Invalid or revoked refresh token"));

    if (storedToken.getExpiresAt().isBefore(Instant.now())) {
      throw new IllegalArgumentException("Refresh token expired");
    }

    storedToken.setRevoked(true);
    refreshTokenRepository.save(storedToken);

    User user =
        userRepository
            .findById(UUID.fromString(userId))
            .orElseThrow(() -> new IllegalArgumentException("User not found"));

    log.info("Token refreshed for user: {}", user.getEmail());
    return buildAuthResponse(user);
  }

  @Transactional
  public void changePassword(UUID userId, ChangePasswordRequest request) {
    User user =
        userRepository
            .findById(userId)
            .orElseThrow(() -> new IllegalArgumentException("User not found"));

    if (!passwordEncoder.matches(request.getCurrentPassword(), user.getPasswordHash())) {
      throw new IllegalArgumentException("Current password is incorrect");
    }

    user.setPasswordHash(passwordEncoder.encode(request.getNewPassword()));
    userRepository.save(user);

    refreshTokenRepository.revokeAllByUserId(userId);
    log.info("Password changed for user: {}", userId);
  }

  @Transactional
  public void logout(UUID userId) {
    refreshTokenRepository.revokeAllByUserId(userId);
    log.info("User logged out: {}", userId);
  }

  private void recordFailedAttempt(User user, Instant now) {
    int failures = user.getFailedLoginAttempts() + 1;
    user.setFailedLoginAttempts(failures);
    if (failures >= LOCKOUT_THRESHOLD) {
      int cycles = user.getLockoutCycles() + 1;
      user.setLockoutCycles(cycles);
      int doublings = Math.min(cycles - 1, MAX_LOCKOUT_DOUBLINGS);
      user.setLockedUntil(now.plus(LOCKOUT_BASE.multipliedBy(1L << doublings)));
      log.warn(
          "Account locked after {} failed login attempts: email={}", failures, user.getEmail());
    }
    userRepository.save(user);
  }

  private AuthResponse buildAuthResponse(User user) {
    String accessToken = jwtTokenProvider.generateAccessToken(user);
    String refreshTokenStr = jwtTokenProvider.generateRefreshToken(user);

    String jti = jwtTokenProvider.extractJti(refreshTokenStr);
    RefreshToken refreshToken = new RefreshToken();
    refreshToken.setUser(user);
    refreshToken.setTokenId(jti);
    refreshToken.setExpiresAt(
        Instant.now().plus(jwtTokenProvider.getRefreshTokenExpiry(), ChronoUnit.SECONDS));
    refreshTokenRepository.save(refreshToken);

    return new AuthResponse(
        accessToken,
        refreshTokenStr,
        "Bearer",
        jwtTokenProvider.getAccessTokenExpiry(),
        new AuthResponse.UserDto(
            user.getId().toString(), user.getEmail(), user.getDisplayName(), user.getAvatarUrl()));
  }
}
