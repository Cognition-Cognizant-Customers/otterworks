# A human (or CI job) who provisions demo tenants, holding the smallest
# credential that can do it.
#
# Tenant creation needs a great deal of authority -- namespaces, Helm releases,
# a database on the shared instance, IRSA trust, DNS records -- and none of it
# belongs on a person's access key. The dashboard already runs that work as a
# runner Job under the control plane's IRSA role, so the only thing a
# provisioner actually needs is the ability to authenticate to the dashboard.
# That reduces the credential to one action on one secret: read the passcode.
#
# Everything else follows from the dashboard's own authorization, which means a
# leaked provisioner key cannot reach the cluster or the AWS estate directly,
# and revoking it is a single key deletion rather than an audit of what the
# holder might have created out of band.

# A dedicated CMK rather than the AWS-managed secretsmanager key, so that access
# to the passcode is auditable and revocable at the key as well as at the
# secret, and rotates on a schedule this module controls. Deliberately not the
# control table's key: the two have different readers, and sharing one would
# quietly widen the control table's key policy to the provisioner.
resource "aws_kms_key" "dashboard_passcode" {
  description             = "Encrypts the Demo Ops dashboard passcode secret"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  tags = {
    Name = "otterworks-dashboard-passcode-${var.environment}"
  }
}

resource "aws_kms_alias" "dashboard_passcode" {
  name          = "alias/otterworks-dashboard-passcode-${var.environment}"
  target_key_id = aws_kms_key.dashboard_passcode.key_id
}

resource "aws_secretsmanager_secret" "dashboard_passcode" {
  name        = "otterworks/${var.environment}/dashboard/passcode"
  description = "Passcode for the Demo Ops dashboard (${var.dns_zone_name != "" ? "ops.${var.dns_zone_name}" : "ops dashboard"})"
  kms_key_id  = aws_kms_key.dashboard_passcode.arn

  # Long enough to undo an accidental delete, short enough that the name can be
  # reused inside a workshop cycle.
  recovery_window_in_days = 7
}

# The value is deliberately not managed here. Terraform would hold the passcode
# in state in plaintext, and the Helm chart is already the thing that decides
# it (`secret.dashboardPasscode`, never on argv). This resource is the container
# and the grant; the value is pushed alongside the chart install:
#
#   kubectl -n otterworks-platform get secret demo-ops-dashboard \
#     -o jsonpath='{.data.DASHBOARD_PASSCODE}' | base64 -d |
#     aws secretsmanager put-secret-value \
#       --secret-id otterworks/dev/dashboard/passcode --secret-string file:///dev/stdin
#
# Rotating the passcode is therefore: helm upgrade with a new value, then the
# command above. Nothing that holds this grant needs to change.

resource "aws_iam_user" "provisioner" {
  name = var.provisioner_user_name
  path = "/demo/"

  tags = {
    Purpose = "Provision demo tenants via the Demo Ops dashboard"
  }
}

data "aws_iam_policy_document" "provisioner" {
  statement {
    sid       = "ReadDashboardPasscode"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.dashboard_passcode.arn]
  }

  # Secrets Manager decrypts on the caller's behalf, so a customer-managed key
  # means the grant above is not sufficient by itself. Narrowed with ViaService
  # so the key cannot be used for anything but reading that secret -- a
  # provisioner holding raw kms:Decrypt could otherwise decrypt any ciphertext
  # produced under this key.
  statement {
    sid       = "DecryptDashboardPasscode"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.dashboard_passcode.arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }

  # Nothing else. In particular: no eks:DescribeCluster, so this key cannot
  # build a kubeconfig even though RBAC would refuse it anyway; and no
  # dynamodb:* on the control table, so tenant state can only be changed
  # through the dashboard's own validation and audit trail rather than by
  # writing items directly.
}

resource "aws_iam_user_policy" "provisioner" {
  name   = "dashboard-access"
  user   = aws_iam_user.provisioner.name
  policy = data.aws_iam_policy_document.provisioner.json
}

# Access keys are created out of band (`aws iam create-access-key --user-name
# de-demo-provisioner`) rather than here: a key in Terraform is a secret in
# state, and this one is meant to be handed to a person and rotated on their
# schedule, not on the platform's.
