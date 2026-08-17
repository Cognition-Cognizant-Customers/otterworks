package com.otterworks.auth.config;

import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.flyway.FlywayConfigurationCustomizer;
import org.springframework.boot.autoconfigure.flyway.FlywayMigrationStrategy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.password.PasswordEncoder;

/**
 * Supplies the seed admin credentials to Flyway as placeholders so no password hash is stored in
 * the migration scripts. When {@code SEED_ADMIN_PASSWORD} is unset the placeholder is empty and the
 * migration seeds no admin user.
 */
@Configuration
public class SeedAdminConfig {

  private static final Logger log = LoggerFactory.getLogger(SeedAdminConfig.class);

  @Bean
  public FlywayConfigurationCustomizer seedAdminPlaceholders(
      @Value("${seed.admin.email:admin@otterworks.dev}") String seedAdminEmail,
      @Value("${seed.admin.password:}") String seedAdminPassword,
      PasswordEncoder passwordEncoder) {
    return configuration -> {
      String passwordHash = "";
      if (seedAdminPassword.isBlank()) {
        log.warn("SEED_ADMIN_PASSWORD is not set; skipping seed admin user creation");
      } else {
        passwordHash = passwordEncoder.encode(seedAdminPassword);
      }
      configuration.placeholders(
          Map.of(
              "seed_admin_email", sqlLiteral(seedAdminEmail),
              "seed_admin_password_hash", sqlLiteral(passwordHash)));
    };
  }

  /**
   * Repairs migration checksums before migrating. V1 dropped its hard-coded seed credentials, so
   * databases migrated before that change would otherwise fail checksum validation on boot.
   */
  @Bean
  public FlywayMigrationStrategy repairBeforeMigrate() {
    return flyway -> {
      flyway.repair();
      flyway.migrate();
    };
  }

  /** Escapes a value for interpolation inside a single-quoted SQL literal. */
  private static String sqlLiteral(String value) {
    return value.replace("'", "''");
  }
}
