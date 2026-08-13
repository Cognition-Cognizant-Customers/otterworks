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

- Ingress is opt-in: `app_ingress_cidr_blocks` defaults to `[]` (no inbound access on 8095).
  Set it to a trusted CIDR to reach the app — the endpoints are unauthenticated plain HTTP.
  No SSH: ops access is via SSM Session Manager (`aws ssm start-session --target <instance_id>`).
- The RDS master password is generated and rotated by RDS itself
  (`manage_master_user_password`) and stored in Secrets Manager; the instance fetches it at
  boot via its IAM role. It never appears in Terraform variables, state, or EC2 user-data.
- RDS sits in the private subnets and only accepts connections from the instance's
  security group.
- Teardown: `terraform destroy` (dev skips the final DB snapshot).
