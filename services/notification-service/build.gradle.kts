plugins {
    kotlin("jvm") version "1.9.23"
    kotlin("plugin.serialization") version "1.9.23"
    id("io.ktor.plugin") version "2.3.9"
    id("com.github.johnrengelman.shadow") version "8.1.1"
}

group = "com.otterworks"
version = "0.1.0"

application {
    mainClass.set("com.otterworks.notification.ApplicationKt")
}

repositories {
    mavenCentral()
}

val ktorVersion = "2.3.9"
val awsSdkVersion = "1.0.70"
val coroutinesVersion = "1.8.0"
val koinVersion = "3.5.3"
val micrometerVersion = "1.12.4"
val serializationVersion = "1.6.3"
val logbackVersion = "1.5.3"
val logstashEncoderVersion = "7.4"
val kotlinLoggingVersion = "3.0.5"
val otelVersion = "1.36.0"
val jedisVersion = "5.1.3"
val kotlinVersion = "1.9.23"
val mockkVersion = "1.13.10"
val commonsTextVersion = "1.9"

dependencies {
    // Ktor Server
    implementation("io.ktor:ktor-server-core-jvm:$ktorVersion")
    implementation("io.ktor:ktor-server-netty-jvm:$ktorVersion")
    implementation("io.ktor:ktor-server-content-negotiation-jvm:$ktorVersion")
    implementation("io.ktor:ktor-server-status-pages-jvm:$ktorVersion")
    implementation("io.ktor:ktor-server-cors-jvm:$ktorVersion")
    implementation("io.ktor:ktor-server-websockets-jvm:$ktorVersion")
    implementation("io.ktor:ktor-server-call-logging-jvm:$ktorVersion")
    implementation("io.ktor:ktor-server-default-headers-jvm:$ktorVersion")
    implementation("io.ktor:ktor-server-metrics-micrometer-jvm:$ktorVersion")
    implementation("io.ktor:ktor-serialization-kotlinx-json-jvm:$ktorVersion")

    // Ktor Client (for calling other services)
    implementation("io.ktor:ktor-client-core-jvm:$ktorVersion")
    implementation("io.ktor:ktor-client-cio-jvm:$ktorVersion")
    implementation("io.ktor:ktor-client-content-negotiation-jvm:$ktorVersion")

    // AWS SDK for Kotlin
    implementation("aws.sdk.kotlin:sqs:$awsSdkVersion")
    implementation("aws.sdk.kotlin:sns:$awsSdkVersion")
    implementation("aws.sdk.kotlin:ses:$awsSdkVersion")
    implementation("aws.sdk.kotlin:dynamodb:$awsSdkVersion")

    // Notification template interpolation
    implementation("org.apache.commons:commons-text:$commonsTextVersion")

    // Serialization
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:$serializationVersion")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:$coroutinesVersion")

    // Dependency Injection - Koin
    implementation("io.insert-koin:koin-core:$koinVersion")
    implementation("io.insert-koin:koin-ktor:$koinVersion")
    implementation("io.insert-koin:koin-logger-slf4j:$koinVersion")

    // Logging
    implementation("ch.qos.logback:logback-classic:$logbackVersion")
    implementation("net.logstash.logback:logstash-logback-encoder:$logstashEncoderVersion")
    implementation("io.github.microutils:kotlin-logging-jvm:$kotlinLoggingVersion")

    // Metrics & Tracing
    implementation("io.micrometer:micrometer-registry-prometheus:$micrometerVersion")
    implementation("io.opentelemetry:opentelemetry-api:$otelVersion")
    implementation("io.opentelemetry:opentelemetry-sdk:$otelVersion")
    implementation("io.opentelemetry:opentelemetry-exporter-otlp:$otelVersion")

    // Redis (chaos flag checks)
    implementation("redis.clients:jedis:$jedisVersion")

    // Testing
    testImplementation("io.ktor:ktor-server-tests-jvm:$ktorVersion")
    testImplementation("io.ktor:ktor-server-test-host:$ktorVersion")
    testImplementation("org.jetbrains.kotlin:kotlin-test-junit:$kotlinVersion")
    testImplementation("io.mockk:mockk:$mockkVersion")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:$coroutinesVersion")
    testImplementation("io.insert-koin:koin-test:$koinVersion")
}

kotlin {
    jvmToolchain(17)
}

tasks.withType<Test> {
    useJUnit()
    // Passthrough for the dependency transcript harness (security/deps); without these
    // properties the emitter test skips itself.
    listOf("ow.deps.cases", "ow.deps.observed").forEach { key ->
        System.getProperty(key)?.let { systemProperty(key, it) }
    }
}
