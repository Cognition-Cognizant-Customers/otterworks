package com.otterworks.analytics.config

import com.typesafe.config.{Config, ConfigFactory}

/** Typed configuration wrapper for the analytics service. */
final case class AppConfig(
    s3: S3Config,
    sqs: SqsConfig,
    aws: AwsConfig,
    eventBridge: EventBridgeConfig,
    postgres: PostgresConfig,
    repository: RepositoryConfig,
    server: ServerConfig
)

final case class S3Config(dataLakeBucket: String)
final case class SqsConfig(eventsQueueUrl: String)
final case class AwsConfig(region: String, endpointUrl: Option[String])

/** EventBridge publishing for the event-driven usage-rollup pipeline. */
final case class EventBridgeConfig(enabled: Boolean, busName: String)
final case class PostgresConfig(
    url: String,
    user: String,
    password: String,
    maxPoolSize: Int
)

/** Selects the metrics store backend: "postgres" (durable, golden default) or "in-memory". */
final case class RepositoryConfig(backend: String):
  def isPostgres: Boolean = backend.trim.toLowerCase == "postgres"

final case class ServerConfig(host: String, port: Int)

object AppConfig:
  def load(): AppConfig =
    val config = ConfigFactory.load()
    fromConfig(config)

  def fromConfig(config: Config): AppConfig =
    val analytics = config.getConfig("analytics")

    val s3 = S3Config(
      dataLakeBucket = analytics.getString("s3.data-lake-bucket")
    )

    val sqs = SqsConfig(
      eventsQueueUrl = analytics.getString("sqs.events-queue-url")
    )

    val aws = AwsConfig(
      region = analytics.getString("aws.region"),
      endpointUrl =
        if analytics.hasPath("aws.endpoint-url") then Some(analytics.getString("aws.endpoint-url"))
        else None
    )

    val eventBridge = EventBridgeConfig(
      enabled = analytics.getBoolean("eventbridge.enabled"),
      busName = analytics.getString("eventbridge.bus-name")
    )

    val pg = analytics.getConfig("postgres")
    val postgres = PostgresConfig(
      url = pg.getString("url"),
      user = pg.getString("user"),
      password = pg.getString("password"),
      maxPoolSize = pg.getInt("max-pool-size")
    )

    val repository = RepositoryConfig(
      backend =
        if analytics.hasPath("repository.backend") then analytics.getString("repository.backend")
        else "postgres"
    )

    val server = ServerConfig(
      host = if analytics.hasPath("server.host") then analytics.getString("server.host") else "0.0.0.0",
      port = if analytics.hasPath("server.port") then analytics.getInt("server.port") else 8088
    )

    AppConfig(s3, sqs, aws, eventBridge, postgres, repository, server)
