val scala3Version = "3.7.0"

lazy val root = project
  .in(file("."))
  .settings(
    name := "analytics-service",
    version := "0.1.0",
    scalaVersion := scala3Version,
    // The HTTP server is the default entrypoint (`java -jar`). The batch job
    // (com.otterworks.analytics.batch.UsageRollupJob) is a second main class run
    // explicitly via `java -cp app.jar ...` (see the analytics-service CronJob).
    Compile / mainClass := Some("com.otterworks.analytics.Main"),
    assembly / mainClass := Some("com.otterworks.analytics.Main"),
    libraryDependencies ++= Seq(
      // Akka HTTP
      "com.typesafe.akka" %% "akka-http" % "10.5.3" cross CrossVersion.for3Use2_13,
      "com.typesafe.akka" %% "akka-actor-typed" % "2.8.8" cross CrossVersion.for3Use2_13,
      "com.typesafe.akka" %% "akka-stream" % "2.8.8" cross CrossVersion.for3Use2_13,
      "com.typesafe.akka" %% "akka-http-spray-json" % "10.5.3" cross CrossVersion.for3Use2_13,

      // JSON
      "io.circe" %% "circe-core" % "0.14.13",
      "io.circe" %% "circe-generic" % "0.14.13",
      "io.circe" %% "circe-parser" % "0.14.13",

      // Database - Slick for PostgreSQL
      "com.typesafe.slick" %% "slick" % "3.6.1" cross CrossVersion.for3Use2_13,
      "com.typesafe.slick" %% "slick-hikaricp" % "3.6.1" cross CrossVersion.for3Use2_13,
      "org.postgresql" % "postgresql" % "42.7.7",

      // Schema migrations (PostgreSQL support is a separate module from Flyway 10)
      "org.flywaydb" % "flyway-core" % "11.8.2",
      "org.flywaydb" % "flyway-database-postgresql" % "11.8.2" % Runtime,

      // AWS SDK
      "software.amazon.awssdk" % "s3" % "2.46.7",
      "software.amazon.awssdk" % "sqs" % "2.46.7",

      // Configuration
      "com.typesafe" % "config" % "1.4.3",

      // Logging
      "ch.qos.logback" % "logback-classic" % "1.5.18",
      "net.logstash.logback" % "logstash-logback-encoder" % "8.1",
      "org.slf4j" % "slf4j-api" % "2.0.17",

      // Metrics
      "io.prometheus" % "simpleclient" % "0.16.0",
      "io.prometheus" % "simpleclient_common" % "0.16.0",
      "io.prometheus" % "simpleclient_hotspot" % "0.16.0",

      // Testing
      "org.scalatest" %% "scalatest" % "3.2.19" % Test,
      "com.typesafe.akka" %% "akka-http-testkit" % "10.5.3" % Test cross CrossVersion.for3Use2_13,
      "com.typesafe.akka" %% "akka-stream-testkit" % "2.8.8" % Test cross CrossVersion.for3Use2_13,
      "com.typesafe.akka" %% "akka-actor-testkit-typed" % "2.8.8" % Test cross CrossVersion.for3Use2_13,

      // Integration testing against a real PostgreSQL (skipped when Docker is unavailable)
      "com.dimafeng" %% "testcontainers-scala-scalatest" % "0.43.0" % Test,
      "com.dimafeng" %% "testcontainers-scala-postgresql" % "0.43.0" % Test,
    ),
    assembly / assemblyMergeStrategy := {
      // Preserve ServiceLoader registrations (Flyway discovers its database
      // and plugin support via META-INF/services).
      case PathList("META-INF", "services", _*) => MergeStrategy.concat
      case PathList("META-INF", _*) => MergeStrategy.discard
      case "reference.conf" => MergeStrategy.concat
      case _ => MergeStrategy.first
    },
  )
