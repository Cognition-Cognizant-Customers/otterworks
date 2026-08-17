FROM gradle:8.6-jdk17 AS builder

WORKDIR /app
COPY build.gradle settings.gradle gradle.lockfile ./
COPY gradle ./gradle
RUN gradle dependencies --no-daemon || true

COPY src ./src
RUN gradle bootJar --no-daemon

FROM eclipse-temurin:17-jre-jammy
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/build/libs/*.jar app.jar

RUN useradd -r -u 1001 appuser
USER appuser

EXPOSE 8081
HEALTHCHECK --interval=15s --timeout=5s CMD curl -f http://localhost:8081/health || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
