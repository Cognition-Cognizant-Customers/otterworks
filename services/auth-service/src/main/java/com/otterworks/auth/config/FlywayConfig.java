package com.otterworks.auth.config;

import java.util.Objects;
import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.MigrationInfo;
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

  /** Checksum V1 had while it still carried the seeded admin insert. */
  private static final Integer SEED_MIGRATION_CHECKSUM = -1077113232;

  /**
   * Databases created before the admin seed was removed from V1 recorded the checksum of the
   * version that carried it, and would fail validation on startup. Repair realigns the schema
   * history for exactly that recorded checksum; every other kind of drift, on V1 or any other
   * migration, still fails startup.
   */
  @Bean
  public FlywayMigrationStrategy seedMigrationAwareStrategy() {
    return flyway -> {
      if (hasSeedMigrationChecksum(flyway)) {
        log.warn(
            "Repairing the schema history: V{} changed when the admin seed moved out of the"
                + " migration",
            SEED_MIGRATION_VERSION);
        flyway.repair();
      }
      flyway.migrate();
    };
  }

  private static boolean hasSeedMigrationChecksum(Flyway flyway) {
    for (MigrationInfo applied : flyway.info().applied()) {
      if (applied.getVersion() != null
          && SEED_MIGRATION_VERSION.equals(applied.getVersion().getVersion())) {
        return Objects.equals(SEED_MIGRATION_CHECKSUM, applied.getChecksum());
      }
    }
    return false;
  }
}
