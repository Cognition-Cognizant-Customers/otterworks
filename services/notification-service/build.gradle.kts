plugins {
    kotlin("jvm") version "2.2.0"
    kotlin("plugin.serialization") version "2.2.0"
    id("io.ktor.plugin") version "3.2.0"
    id("com.gradleup.shadow") version "8.3.6"
}

group = "com.otterworks"
version = "0.1.0"

application {
    mainClass.set("com.otterworks.notification.ApplicationKt")
}

repositories {
    mavenCentral()
}

val ktorVersion = "3.2.0"
val awsSdkVersion = "1.6.92"
val coroutinesVersion = "1.10.2"
val koinVersion = "4.1.0"
val micrometerVersion = "1.15.0"

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

    // Serialization
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:$coroutinesVersion")

    // Dependency Injection - Koin
    implementation("io.insert-koin:koin-core:$koinVersion")
    implementation("io.insert-koin:koin-ktor:$koinVersion")
    implementation("io.insert-koin:koin-logger-slf4j:$koinVersion")

    // Logging
    implementation("ch.qos.logback:logback-classic:1.5.18")
    implementation("net.logstash.logback:logstash-logback-encoder:8.1")
    implementation("io.github.oshai:kotlin-logging-jvm:7.0.7")

    // Metrics & Tracing
    implementation("io.micrometer:micrometer-registry-prometheus:$micrometerVersion")
    implementation("io.opentelemetry:opentelemetry-api:1.51.0")
    implementation("io.opentelemetry:opentelemetry-sdk:1.51.0")
    implementation("io.opentelemetry:opentelemetry-exporter-otlp:1.51.0")

    // Redis (chaos flag checks)
    implementation("redis.clients:jedis:6.0.0")

    // Testing
    testImplementation("io.ktor:ktor-server-test-host:$ktorVersion")
    testImplementation("org.jetbrains.kotlin:kotlin-test-junit:2.2.0")
    testImplementation("io.mockk:mockk:1.14.3")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:$coroutinesVersion")
    testImplementation("io.insert-koin:koin-test:$koinVersion")
}

kotlin {
    jvmToolchain(17)
}

tasks.withType<Test> {
    useJUnit()
}
