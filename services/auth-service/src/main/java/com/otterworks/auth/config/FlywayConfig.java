package com.otterworks.auth.config;

import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.autoconfigure.flyway.FlywayMigrationStrategy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Validates the seed password placeholder and, during the transition off the previously committed
 * admin password digest, repairs the schema history before migrating so that the V1 checksum change
 * does not block startup on databases that already applied V1. Set {@code
 * auth.flyway.repair-before-migrate=false} (env {@code AUTH_FLYWAY_REPAIR_BEFORE_MIGRATE}) to
 * restore Flyway's default checksum validation once every database has applied V5.
 */
@Configuration
public class FlywayConfig {

  private final String seedAdminPassword;

  public FlywayConfig(
      @Value("${spring.flyway.placeholders.seedAdminPassword:}") String seedAdminPassword) {
    this.seedAdminPassword = seedAdminPassword;
  }

  /**
   * Flyway substitutes placeholders textually, so a value containing a single quote would either
   * break the seed migrations or inject SQL. Fail fast with an actionable message instead.
   */
  @PostConstruct
  public void validateSeedAdminPassword() {
    if (seedAdminPassword.indexOf('\'') >= 0) {
      throw new IllegalStateException(
          "AUTH_SEED_ADMIN_PASSWORD must not contain a single quote: it is substituted textually"
              + " into the seed migrations.");
    }
  }

  @Bean
  @ConditionalOnProperty(
      name = "auth.flyway.repair-before-migrate",
      havingValue = "true",
      matchIfMissing = true)
  public FlywayMigrationStrategy repairBeforeMigrate() {
    return flyway -> {
      flyway.repair();
      flyway.migrate();
    };
  }
}
