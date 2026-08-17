package com.otterworks.auth.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "auth.admin-seed")
@Getter
@Setter
public class AdminSeedProperties {

  private boolean enabled = true;
  private String email;
  private String displayName;

  /** Plain-text password, hashed with the configured encoder before it is stored. */
  private String password;

  /** Pre-computed BCrypt hash, used in preference to {@link #password} when both are set. */
  private String passwordHash;
}
