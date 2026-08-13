# Rehost: legacy-portal → EC2 + RDS (lift-and-shift)

Standalone Terraform root that lifts [`services/legacy-portal`](../../../services/legacy-portal)
from its on-prem/VM deployment onto AWS **as-is** — no re-architecture:

| On-prem today | Rehosted |
|---|---|
| Fat JAR under systemd on a VM (`deploy/legacy-portal.service`) | Same JAR, same systemd unit, on one EC2 instance (Amazon Linux 2023, Corretto 11) |
| Co-located PostgreSQL (`docker-compose.onprem.yml`) | RDS PostgreSQL 15 (`legacyportal` DB, private subnets) |
| `scripts/initdb.sql` schemas at DB init | Same three schemas created by user-data at first boot |
| Copy JAR to `/opt/legacy-portal` by hand | `scripts/rehost-deploy.sh` (build → S3 → SSM restart) |

Intentionally separate from the EKS/Helm path — this is the rehost demo, not a re-platform.
The VPC is reused from `/platform/terraform` via remote state; everything else
(EC2, RDS, security groups, artifact bucket, IAM role) is owned by this root.

## Provision

```bash
cd infrastructure/terraform/rehost
terraform init
export TF_VAR_db_password='...'    # do not commit real secrets
terraform apply
```

## Deploy / redeploy the app

```bash
./scripts/rehost-deploy.sh          # from the repo root
curl "$(terraform -chdir=infrastructure/terraform/rehost output -raw app_url)/health"
```

The instance's user-data also pulls the JAR at first boot, so a fresh `apply` after an
upload comes up running without a separate deploy step.

## Notes

- The instance is reachable only on port 8095 (`app_ingress_cidr_blocks`, default open for
  demo purposes — restrict it for anything longer-lived). No SSH: ops access is via SSM
  Session Manager (`aws ssm start-session --target <instance_id>`).
- RDS sits in the private subnets and only accepts connections from the instance's
  security group.
- Teardown: `terraform destroy` (dev skips the final DB snapshot).
