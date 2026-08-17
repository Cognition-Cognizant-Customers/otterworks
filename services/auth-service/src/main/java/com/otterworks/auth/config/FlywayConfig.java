package com.otterworks.auth.config;

import org.flywaydb.core.api.output.ValidateOutput;
import org.flywaydb.core.api.output.ValidateResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.flyway.FlywayMigrationStrategy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class FlywayConfig {

  private static final Logger log = LoggerFactory.getLogger(FlywayConfig.class);

  /** Version of the migration the admin seed was removed from. */
  private static final String SEED_MIGRATION_VERSION = "1";

  private static final String CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH";

  private static final String NOT_APPLIED_SUFFIX = "_NOT_APPLIED";

  /**
   * Databases created before the admin seed was removed from V1 record its old checksum and would
   * otherwise fail startup. Repair runs only when validation fails for exactly that reason on
   * exactly that migration, so any other checksum drift still fails loudly.
   */
  @Bean
  public FlywayMigrationStrategy seedMigrationAwareStrategy() {
    return flyway -> {
      ValidateResult validation = flyway.validateWithResult();
      if (!validation.validationSuccessful && isSeedMigrationChecksumOnly(validation)) {
        log.warn(
            "Repairing the schema history: V{} changed when the admin seed moved out of the"
                + " migration",
            SEED_MIGRATION_VERSION);
        flyway.repair();
      }
      flyway.migrate();
    };
  }

  private static boolean isSeedMigrationChecksumOnly(ValidateResult validation) {
    boolean seedMigrationMismatch = false;
    for (ValidateOutput invalid : validation.invalidMigrations) {
      String errorCode = invalid.errorDetails.errorCode.name();
      if (errorCode.endsWith(NOT_APPLIED_SUFFIX)) {
        // Migrations this upgrade still has to apply, V5 among them.
        continue;
      }
      if (!SEED_MIGRATION_VERSION.equals(invalid.version) || !CHECKSUM_MISMATCH.equals(errorCode)) {
        return false;
      }
      seedMigrationMismatch = true;
    }
    return seedMigrationMismatch;
  }
}
