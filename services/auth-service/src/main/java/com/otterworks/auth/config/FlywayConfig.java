package com.otterworks.auth.config;

import org.springframework.boot.autoconfigure.flyway.FlywayMigrationStrategy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Repairs the schema history before migrating so that a checksum change in an already-applied
 * migration does not block startup on long-lived databases.
 */
@Configuration
public class FlywayConfig {

  @Bean
  public FlywayMigrationStrategy repairBeforeMigrate() {
    return flyway -> {
      flyway.repair();
      flyway.migrate();
    };
  }
}
