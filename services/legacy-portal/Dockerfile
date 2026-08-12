# Java 21 Maven build, matching the legacy Java enterprise pattern.
# NOTE: legacy-portal deploys to a VM / on-prem host today (see docker-compose.onprem.yml,
# scripts/run-onprem.sh, deploy/legacy-portal.service). It is intentionally NOT on the Helm/EKS
# deploy path — it is the rehost / decomposition "before" state.
FROM maven:3.9-eclipse-temurin-21 AS builder

WORKDIR /app
COPY pom.xml .
# Download dependencies first for Docker layer caching.
RUN mvn -B dependency:go-offline

COPY src/ src/
RUN mvn -B package -DskipTests

FROM eclipse-temurin:21-jre-jammy

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/target/legacy-portal.jar app.jar

RUN useradd -r -u 1001 appuser
USER appuser

EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8095/health || exit 1

ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75.0", "-XX:+ExitOnOutOfMemoryError", "-jar", "app.jar"]
