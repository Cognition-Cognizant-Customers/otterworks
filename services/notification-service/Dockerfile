FROM gradle:8.14-jdk17 AS builder

WORKDIR /app
COPY build.gradle.kts settings.gradle.kts ./
RUN gradle dependencies --no-daemon || true

COPY src ./src
RUN gradle shadowJar --no-daemon

FROM eclipse-temurin:17-jre-jammy
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/build/libs/*-all.jar app.jar

RUN useradd -r -u 1001 appuser
USER appuser

EXPOSE 8086
HEALTHCHECK --interval=10s --timeout=3s CMD curl -f http://localhost:8086/health || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
