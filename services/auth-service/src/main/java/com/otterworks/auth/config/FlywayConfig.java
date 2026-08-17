package com.otterworks.auth.config;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.autoconfigure.flyway.FlywayMigrationStrategy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class FlywayConfig {

  /**
   * Opt-in strategy for databases created before the admin seed was removed from V1: repairing the
   * schema history realigns the resulting checksum change instead of failing startup. Left off by
   * default so checksum validation keeps protecting other environments.
   */
  @Bean
  @ConditionalOnProperty(name = "auth.flyway.repair-on-migrate", havingValue = "true")
  public FlywayMigrationStrategy repairingFlywayMigrationStrategy() {
    return flyway -> {
      flyway.repair();
      flyway.migrate();
    };
  }
}
