package com.otterworks.auth.config;

import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/**
 * Seeds the admin account from {@code SEED_ADMIN_EMAIL} / {@code SEED_ADMIN_PASSWORD} on every
 * startup, so no password hash is stored in the repository and rotating the configured password
 * takes effect on the next deploy. When no password is configured nothing is seeded.
 */
@Component
public class SeedAdminInitializer implements ApplicationRunner {

  private static final Logger log = LoggerFactory.getLogger(SeedAdminInitializer.class);
  private static final UUID SEED_ADMIN_ID = UUID.fromString("a0000000-0000-0000-0000-000000000001");

  private final JdbcTemplate jdbcTemplate;
  private final PasswordEncoder passwordEncoder;
  private final String seedAdminEmail;
  private final String seedAdminPassword;

  public SeedAdminInitializer(
      JdbcTemplate jdbcTemplate,
      PasswordEncoder passwordEncoder,
      @Value("${seed.admin.email:admin@otterworks.dev}") String seedAdminEmail,
      @Value("${seed.admin.password:}") String seedAdminPassword) {
    this.jdbcTemplate = jdbcTemplate;
    this.passwordEncoder = passwordEncoder;
    this.seedAdminEmail = seedAdminEmail;
    this.seedAdminPassword = seedAdminPassword;
  }

  @Override
  public void run(ApplicationArguments args) {
    if (seedAdminPassword.isBlank()) {
      log.warn(
          "SEED_ADMIN_PASSWORD is not set; the admin account is not seeded and any credential it"
              + " already carries is left unchanged");
      return;
    }

    jdbcTemplate.update(
        """
        INSERT INTO users (id, email, password_hash, display_name, email_verified,
                           created_at, updated_at)
        VALUES (?, ?, ?, 'Admin User', true, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE
        SET email = EXCLUDED.email,
            password_hash = EXCLUDED.password_hash,
            updated_at = NOW()
        """,
        SEED_ADMIN_ID,
        seedAdminEmail,
        passwordEncoder.encode(seedAdminPassword));

    jdbcTemplate.update(
        """
        INSERT INTO user_roles (user_id, role)
        SELECT ?, role FROM (VALUES ('ADMIN'), ('USER')) AS seeded_roles(role)
        ON CONFLICT DO NOTHING
        """,
        SEED_ADMIN_ID);

    log.info("Seeded admin user {} from the configured seed credentials", seedAdminEmail);
  }
}
