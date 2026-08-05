# Maven build instead of Gradle (matches legacy Java enterprise pattern)
FROM maven:3.9.6-eclipse-temurin-17 AS builder

WORKDIR /app
COPY pom.xml .
# Download dependencies first for Docker layer caching
RUN mvn dependency:go-offline -B

COPY src/ src/
RUN mvn package -DskipTests -B

FROM eclipse-temurin:17-jre

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p /tmp/reports

COPY --from=builder /app/target/report-service.jar app.jar

RUN useradd -r -u 1001 appuser && chown appuser:appuser /tmp/reports
USER appuser

EXPOSE 8091

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8091/health || exit 1

ENTRYPOINT ["java", "-jar", "app.jar"]
