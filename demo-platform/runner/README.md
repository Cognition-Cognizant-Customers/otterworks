# demo-runner image

The image the ops dashboard launches as a Kubernetes **Job** to run one tenant
operation, and that the reaper CronJob runs with `OP=reap`. It carries the repo
plus `bash / git / aws-cli v2 / kubectl / helm / terraform / jq / psql` and is
thin glue over the existing scripts in `../../scripts`.

## Build (from the repo root)

The build context must be the repo root so `scripts/` + `demo-platform/` are
copied in:

```bash
docker build -f demo-platform/runner/Dockerfile \
  -t <registry>/otterworks/demo-runner:<tag> .
docker push <registry>/otterworks/demo-runner:<tag>
```

## Operations (env `OP`)

| OP | Runs | Control-table writes |
|---|---|---|
| `deploy`   | `scripts/deploy-tenant.sh`   | `deploying` → `active`/`error`, `url`/`api_url`/`db_name`/`namespace`/`expires_at`; audit `checkout`,`deploy_ok`/`deploy_fail` |
| `teardown` | `scripts/teardown-tenant.sh` | `draining` → `free`; audit `checkin` |
| `inject`   | `scripts/inject-bug.sh <id> <scenario>` | audit `inject` |
| `reset`    | `scripts/inject-bug.sh <id> reset`      | audit `reset` |
| `reap`     | `demo-platform/reaper/reaper.sh`        | reaper GC + audit `reap` |

## Environment

Non-secret (control-plane metadata): `OP`, `TENANT_ID`, `TIER`, `TTL`,
`IMAGE_TAG`, `HOST_SUFFIX` (default `demo.otterworks.app`), `SCENARIO`,
`TENANT_BRANCH`, `CONTROL_TABLE` (default `otterworks-demo-control`),
`AWS_REGION`, `EKS_CLUSTER` (default `otterworks-dev`), `ACTOR`.

Secrets (from Kubernetes Secret refs — **env only, never argv**): `DB_PASSWORD`,
`JWT_SECRET`, `SECRET_KEY_BASE`. AWS creds come from the pod's IRSA role.

## Example Job (deploy)

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: deploy-a01-1720000000
  namespace: otterworks-platform
spec:
  backoffLimit: 1
  ttlSecondsAfterFinished: 600
  template:
    spec:
      serviceAccountName: demo-ops-dashboard
      restartPolicy: Never
      containers:
        - name: runner
          image: <registry>/otterworks/demo-runner:<tag>
          env:
            - { name: OP, value: deploy }
            - { name: TENANT_ID, value: a01 }
            - { name: TENANT_BRANCH, value: workshop-a01 }
            - { name: TIER, value: A }
            - { name: TTL, value: 8h }
            - name: DB_PASSWORD
              valueFrom: { secretKeyRef: { name: demo-ops-dashboard, key: DB_PASSWORD } }
```
