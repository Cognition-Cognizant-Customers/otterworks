package com.otterworks.auth.config;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.autoconfigure.flyway.FlywayMigrationStrategy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Repairs the schema history before migrating so that the V1 checksum change that removed the
 * committed admin password digest does not block startup on databases that already applied V1.
 * Disable with {@code auth.flyway.repair-before-migrate=false} to restore Flyway's default checksum
 * validation once every database has been migrated.
 */
@Configuration
public class FlywayConfig {

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
