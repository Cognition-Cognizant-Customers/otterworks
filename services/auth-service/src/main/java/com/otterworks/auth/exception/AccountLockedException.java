package com.otterworks.auth.exception;

import java.time.Instant;

/** Thrown when an account is temporarily locked after repeated failed logins. */
public class AccountLockedException extends RuntimeException {

  private final Instant lockedUntil;

  public AccountLockedException(Instant lockedUntil) {
    super("Account temporarily locked due to repeated failed login attempts");
    this.lockedUntil = lockedUntil;
  }

  public Instant getLockedUntil() {
    return lockedUntil;
  }
}
