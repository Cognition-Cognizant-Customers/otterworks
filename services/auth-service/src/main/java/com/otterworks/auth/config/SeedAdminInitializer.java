package com.otterworks.auth.config;

import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/**
 * Seeds the admin account from {@code SEED_ADMIN_EMAIL} / {@code SEED_ADMIN_PASSWORD} at startup,
 * so no password hash is stored in the repository. The account is only created when it is missing;
 * credentials changed after that are left alone unless {@code SEED_ADMIN_ROTATE_PASSWORD=true} asks
 * for the configured password to be applied again. Nothing is seeded when no password is
 * configured.
 */
@Component
public class SeedAdminInitializer implements ApplicationRunner {

  private static final Logger log = LoggerFactory.getLogger(SeedAdminInitializer.class);
  private static final UUID SEED_ADMIN_ID = UUID.fromString("a0000000-0000-0000-0000-000000000001");

  private static final String INSERT_ADMIN =
      """
      INSERT INTO users (id, email, password_hash, display_name, email_verified,
                         created_at, updated_at)
      VALUES (?, ?, ?, 'Admin User', true, NOW(), NOW())
      ON CONFLICT (id) DO NOTHING
      """;

  private static final String ROTATE_ADMIN =
      """
      UPDATE users SET email = ?, password_hash = ?, updated_at = NOW()
      WHERE id = ?
      """;

  private static final String INSERT_ROLES =
      """
      INSERT INTO user_roles (user_id, role)
      SELECT ?, role FROM (VALUES ('ADMIN'), ('USER')) AS seeded_roles(role)
      ON CONFLICT DO NOTHING
      """;

  private final JdbcTemplate jdbcTemplate;
  private final PasswordEncoder passwordEncoder;
  private final String seedAdminEmail;
  private final String seedAdminPassword;
  private final boolean rotatePassword;

  public SeedAdminInitializer(
      JdbcTemplate jdbcTemplate,
      PasswordEncoder passwordEncoder,
      @Value("${seed.admin.email:admin@otterworks.dev}") String seedAdminEmail,
      @Value("${seed.admin.password:}") String seedAdminPassword,
      @Value("${seed.admin.rotate-password:false}") boolean rotatePassword) {
    this.jdbcTemplate = jdbcTemplate;
    this.passwordEncoder = passwordEncoder;
    this.seedAdminEmail = seedAdminEmail;
    this.seedAdminPassword = seedAdminPassword;
    this.rotatePassword = rotatePassword;
  }

  @Override
  public void run(ApplicationArguments args) {
    if (seedAdminPassword.isBlank()) {
      log.warn(
          "SEED_ADMIN_PASSWORD is not set; the admin account is not seeded and any credential it"
              + " already carries is left unchanged");
      return;
    }

    try {
      seedAdmin();
    } catch (DataAccessException e) {
      log.error(
          "Failed to seed the admin account {}; the service continues without it",
          seedAdminEmail,
          e);
    }
  }

  private void seedAdmin() {
    int inserted =
        jdbcTemplate.update(
            INSERT_ADMIN, SEED_ADMIN_ID, seedAdminEmail, passwordEncoder.encode(seedAdminPassword));

    if (inserted > 0) {
      log.info("Seeded admin user {} from the configured seed credentials", seedAdminEmail);
    } else if (rotatePassword) {
      jdbcTemplate.update(
          ROTATE_ADMIN, seedAdminEmail, passwordEncoder.encode(seedAdminPassword), SEED_ADMIN_ID);
      log.info("Rotated admin user {} to the configured seed credentials", seedAdminEmail);
    } else {
      log.info(
          "Admin user already exists; leaving its credentials untouched (set"
              + " SEED_ADMIN_ROTATE_PASSWORD=true to apply the configured password)");
    }

    jdbcTemplate.update(INSERT_ROLES, SEED_ADMIN_ID);
  }
}
