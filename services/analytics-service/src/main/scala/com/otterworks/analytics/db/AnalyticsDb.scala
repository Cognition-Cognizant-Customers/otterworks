package com.otterworks.analytics.db

import com.otterworks.analytics.config.PostgresConfig
import org.flywaydb.core.Flyway
import org.slf4j.LoggerFactory
import slick.jdbc.PostgresProfile.api.*

/** A persisted daily aggregate rollup entry. */
final case class DailyMetric(eventDate: String, eventType: String, eventCount: Long)

/**
 * Owns the Slick database handle and applies the analytics schema via Flyway
 * (the same `db/migration` convention used by the JVM services in this repo).
 *
 * Queries use Slick plain SQL rather than the lifted (Table/TableQuery) API:
 * Slick 3.5's `mapTo`/`TableQuery[T]` rely on Scala 2 macros that cannot run
 * under the Scala 3 compiler, whereas plain SQL is macro-free.
 */
class AnalyticsDb(config: PostgresConfig):
  private val logger = LoggerFactory.getLogger(getClass)

  // All analytics tables live in a dedicated `analytics` schema with a
  // service-owned Flyway history table, so this service never races other
  // JVM services over the shared public.flyway_schema_history.
  private val schema = "analytics"
  private val urlWithSchema =
    if config.url.contains("?") then s"${config.url}&currentSchema=$schema"
    else s"${config.url}?currentSchema=$schema"

  val database: Database = Database.forURL(
    url = urlWithSchema,
    user = config.user,
    password = config.password,
    driver = "org.postgresql.Driver",
    executor = AsyncExecutor("analytics-db", numThreads = config.maxPoolSize, queueSize = 1000)
  )

  /** Apply pending schema migrations from classpath `db/migration`. */
  def migrate(): Unit =
    val result = Flyway
      .configure()
      .dataSource(config.url, config.user, config.password)
      .locations("classpath:db/migration")
      .defaultSchema(schema)
      .createSchemas(true)
      .table("flyway_schema_history_analytics")
      .load()
      .migrate()
    logger.info(
      "Analytics schema migrated to version {} ({} migrations applied)",
      Option(result.targetSchemaVersion).getOrElse("current"),
      result.migrationsExecuted
    )

  def close(): Unit = database.close()
