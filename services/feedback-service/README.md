# Feedback Service

User feedback microservice, extracted from the `legacy-portal` monolith's feedback bounded
context. Java 17 / Spring Boot 3.2 / Gradle, mirroring `auth-service`.

It owns the `feedback` database schema exclusively (its datasource points at that schema via
`?currentSchema=feedback` and its Flyway migrations manage only that schema). No other service —
including the monolith — may reference its tables.

## Routes

| Route | Description |
|---|---|
| `POST /api/feedback` | Submit feedback (`userId`, `rating` 1-5, `message`) → 201 |
| `GET /api/feedback?userId=` | List a user's feedback, newest first |
| `GET /api/feedback/average-rating` | Average rating across all feedback |

Payloads and status codes are identical to the routes previously served by
`legacy-portal` (see that service's README for the decomposition story).

## Build & test

```bash
cd services/feedback-service
./gradlew build        # compile + tests + spotless + jacoco
./gradlew bootRun      # run on :8085 (needs PostgreSQL; see application.yml)
```
