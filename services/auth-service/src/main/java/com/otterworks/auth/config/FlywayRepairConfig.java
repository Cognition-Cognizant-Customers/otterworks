package com.otterworks.auth.config;

import org.flywaydb.core.api.exception.FlywayValidateException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.autoconfigure.flyway.FlywayMigrationStrategy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Recovers from the one-off checksum drift introduced when V1 dropped its hard-coded seed
 * credentials: databases migrated before that change fail validation once, are repaired, and then
 * migrate normally. Validation still runs first, so an unexpected checksum change is always logged.
 * Set {@code flyway.repair-on-validation-error=false} to fail fast instead.
 */
@Configuration
@ConditionalOnProperty(
    name = "flyway.repair-on-validation-error",
    havingValue = "true",
    matchIfMissing = true)
public class FlywayRepairConfig {

  private static final Logger log = LoggerFactory.getLogger(FlywayRepairConfig.class);

  @Bean
  public FlywayMigrationStrategy repairOnValidationError() {
    return flyway -> {
      try {
        flyway.migrate();
      } catch (FlywayValidateException e) {
        log.warn(
            "Flyway validation failed; repairing migration checksums and retrying. Expected once"
                + " per database for the V1 seed-credential removal, otherwise investigate.",
            e);
        flyway.repair();
        flyway.migrate();
      }
    };
  }
}
