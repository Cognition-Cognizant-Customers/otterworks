# Parity contract: on-prem → container platform

`legacy-portal` historically ran on a VM (fat JAR under systemd / plain Docker Compose,
see `../deploy/legacy-portal.service` and `../docker-compose.onprem.yml`). It now also has
a chart on the shared Helm/EKS path (`infrastructure/helm/legacy-portal`), wired through
`scripts/deploy-dev.sh` and `scripts/lib/tenant-common.sh` like every other service.
**Both paths stay runnable** — this directory proves the two behave identically.

## Files

| File | Deployment it was captured against |
|---|---|
| `before-onprem.txt` | On-prem VM path: `scripts/run-onprem.sh` (fat JAR, embedded H2) |
| `before-onprem-compose.txt` | On-prem VM path: `docker-compose.onprem.yml` (app + PostgreSQL, schemas from `scripts/initdb.sql`) |
| `after-platform.txt` | Container-platform path: the image from `../Dockerfile` run with exactly the env the Helm chart's ConfigMap/Secret supplies (`SPRING_PROFILES_ACTIVE=postgres`, datasource URL/user/password, `JAVA_TOOL_OPTIONS=-Dspring.jpa.properties.hibernate.hbm2ddl.create_namespaces=true`) against a fresh PostgreSQL with **no** initdb hook |

All three transcripts are byte-identical (`diff` is empty) after normalizing volatile
fields (`id`, `createdAt`, error timestamps). They cover every route of the three bounded
contexts (announcements, user-preferences, feedback) plus `/health` and `/actuator/health`,
including 404 error paths.

## Re-running

```bash
# Before (on-prem):
./scripts/run-onprem.sh &            # or: docker compose -f docker-compose.onprem.yml up -d
BASE_URL=http://localhost:8095 ./parity/capture-transcript.sh /tmp/before.txt

# After (platform config):
docker build -t legacy-portal:local .
docker run -d --name db --network pn -e POSTGRES_DB=lp -e POSTGRES_USER=lp -e POSTGRES_PASSWORD=lp postgres:15-alpine
docker run -d --network pn -p 8095:8095 \
  -e SPRING_PROFILES_ACTIVE=postgres \
  -e SPRING_DATASOURCE_URL=jdbc:postgresql://db:5432/lp \
  -e SPRING_DATASOURCE_USERNAME=lp -e SPRING_DATASOURCE_PASSWORD=lp \
  -e 'JAVA_TOOL_OPTIONS=-Dspring.jpa.properties.hibernate.hbm2ddl.create_namespaces=true' \
  legacy-portal:local
BASE_URL=http://localhost:8095 ./parity/capture-transcript.sh /tmp/after.txt

diff /tmp/before.txt /tmp/after.txt   # must be empty
```
