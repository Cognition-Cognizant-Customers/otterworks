package com.otterworks.auth.service;

import com.otterworks.auth.config.AdminSeedProperties;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

/**
 * Seeds the local/demo admin account from configuration instead of from a database migration, so
 * that no password material lives in source control.
 */
@Component
public class AdminUserSeeder implements ApplicationRunner {

  private static final Logger log = LoggerFactory.getLogger(AdminUserSeeder.class);
  private static final UUID ADMIN_ID = UUID.fromString("a0000000-0000-0000-0000-000000000001");

  private final AdminSeedProperties properties;
  private final PasswordEncoder passwordEncoder;
  private final JdbcTemplate jdbcTemplate;

  public AdminUserSeeder(
      AdminSeedProperties properties, PasswordEncoder passwordEncoder, JdbcTemplate jdbcTemplate) {
    this.properties = properties;
    this.passwordEncoder = passwordEncoder;
    this.jdbcTemplate = jdbcTemplate;
  }

  @Override
  @Transactional
  public void run(ApplicationArguments args) {
    if (!properties.isEnabled()) {
      return;
    }
    if (!StringUtils.hasText(properties.getEmail())) {
      log.info("Admin seeding skipped: auth.admin-seed.email is not configured");
      return;
    }

    String hash = resolveHash();
    if (hash == null) {
      log.warn(
          "Admin seeding skipped: set AUTH_ADMIN_SEED_PASSWORD or AUTH_ADMIN_SEED_PASSWORD_HASH"
              + " to provision {}",
          properties.getEmail());
      return;
    }

    jdbcTemplate.update(
        """
        INSERT INTO users (id, email, password_hash, display_name, email_verified,
                           created_at, updated_at)
        VALUES (?, ?, ?, ?, true, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE
        SET email = EXCLUDED.email,
            password_hash = EXCLUDED.password_hash,
            display_name = EXCLUDED.display_name,
            email_verified = true,
            updated_at = NOW()
        """,
        ADMIN_ID,
        properties.getEmail(),
        hash,
        StringUtils.hasText(properties.getDisplayName())
            ? properties.getDisplayName()
            : "Admin User");

    for (String role : new String[] {"ADMIN", "USER"}) {
      jdbcTemplate.update(
          "INSERT INTO user_roles (user_id, role) VALUES (?, ?) ON CONFLICT DO NOTHING",
          ADMIN_ID,
          role);
    }

    log.info("Seeded admin user {}", properties.getEmail());
  }

  private String resolveHash() {
    if (StringUtils.hasText(properties.getPasswordHash())) {
      return properties.getPasswordHash();
    }
    if (StringUtils.hasText(properties.getPassword())) {
      return passwordEncoder.encode(properties.getPassword());
    }
    return null;
  }
}
