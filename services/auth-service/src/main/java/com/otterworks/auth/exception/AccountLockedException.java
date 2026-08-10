package com.otterworks.auth.exception;

/** Thrown when a login is refused because the account is temporarily locked. */
public class AccountLockedException extends RuntimeException {

  public AccountLockedException(String message) {
    super(message);
  }
}
