# IRSA role assumed by the dashboard web pod + runner Jobs (SA
# otterworks-platform:demo-ops-dashboard). Deliberately scoped — this is NOT the
# broad otterworks-* tenant wildcard; it grants only what the control plane needs.
data "aws_iam_policy_document" "dashboard_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:sub"
      values   = [local.dashboard_sa]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dashboard" {
  name               = "otterworks-demo-ops-dashboard-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.dashboard_trust.json
}

locals {
  # The platform's own cluster is always sweepable; var.sweepable_clusters adds
  # names it used to run under, whose orphans still need reclaiming.
  sweepable_clusters = toset(concat([var.cluster_name], var.sweepable_clusters))
}

data "aws_iam_policy_document" "dashboard" {
  # Full control of the control table (+ any indexes).
  statement {
    sid    = "ControlTable"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
      "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem", "dynamodb:DescribeTable",
    ]
    resources = [
      aws_dynamodb_table.control.arn,
      "${aws_dynamodb_table.control.arn}/index/*",
    ]
  }

  # Use the control table's customer-managed CMK (DynamoDB SSE). DynamoDB
  # decrypts/encrypts items on this principal's behalf via the key.
  statement {
    sid    = "ControlTableKms"
    effect = "Allow"
    actions = [
      "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey",
    ]
    resources = [aws_kms_key.control.arn]
  }

  # Reaper GC of per-tenant items in the SHARED app tables.
  statement {
    sid    = "SharedTenantDataGC"
    effect = "Allow"
    actions = [
      "dynamodb:Query", "dynamodb:Scan", "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem", "dynamodb:DescribeTable",
    ]
    resources = ["arn:aws:dynamodb:${var.aws_region}:${local.account_id}:table/${var.shared_dynamodb_table_prefix}*"]
  }

  # Reaper GC of per-tenant object prefixes in the SHARED app buckets.
  statement {
    sid       = "SharedBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.shared_s3_bucket_prefix}*"]
  }
  statement {
    sid       = "SharedBucketObjectGC"
    effect    = "Allow"
    actions   = ["s3:DeleteObject", "s3:GetObject"]
    resources = ["arn:aws:s3:::${var.shared_s3_bucket_prefix}*/*"]
  }

  # Read cluster info to build a kubeconfig (k8s authz is via RBAC, see helm/).
  statement {
    sid       = "DescribeCluster"
    effect    = "Allow"
    actions   = ["eks:DescribeCluster"]
    resources = [data.aws_eks_cluster.this.arn]
  }

  # The infrastructure sweep (infra-sweep.sh) reclaims the AWS resources
  # Kubernetes creates implicitly and does not clean up when a cluster is
  # replaced -- load balancers, target groups, volumes, addresses, the
  # k8s-elb-* security groups.
  #
  # Reading the estate has to be account-wide: finding an orphan means looking
  # at resources whose owner is not yet known. Deleting does not, and must not
  # -- this account also holds unrelated workloads, including a reserved address
  # that an early version of this sweep would have released. The deletes below
  # are therefore conditioned on the resource carrying the ownership tag of a
  # cluster this platform is responsible for, so the account-wide blast radius
  # of the sweep script being wrong, misconfigured or abused is bounded by IAM
  # rather than by the script's own DRY_RUN and tag checks.
  #
  # eks:ListClusters is what decides which clusters are live. Without it the
  # sweep cannot tell an orphan from a running platform, and it correctly
  # refuses to run at all.
  statement {
    sid    = "InfraOrphanSweepRead"
    effect = "Allow"
    actions = [
      "eks:ListClusters",
      "elasticloadbalancing:DescribeLoadBalancers",
      "elasticloadbalancing:DescribeTargetGroups",
      "elasticloadbalancing:DescribeTags",
      "ec2:DescribeVolumes",
      "ec2:DescribeAddresses",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeInstances",
    ]
    resources = ["*"]
  }

  # One statement per sweepable cluster: IAM matches a tag key exactly, and
  # several keys in a single condition are ANDed, which no resource would
  # satisfy. Keep local.sweepable_clusters in step with SWEEPABLE_CLUSTERS in the
  # reaper CronJob -- a cluster in one and not the other means the sweep either
  # cannot delete its orphans, or reports them and is refused by IAM.
  #
  # This covers Classic ELB as well as ELBv2. The 2012-06-01 Classic API is
  # widely documented as having no resource-level permissions, which is no
  # longer true; verified against the live API with a role holding only the
  # conditioned delete below: an untagged Classic ELB returns AccessDenied, a
  # tagged one is deleted. So there is no reason to exempt it.
  dynamic "statement" {
    for_each = local.sweepable_clusters
    content {
      sid    = "InfraOrphanSweepDelete${replace(title(replace(statement.value, "-", " ")), " ", "")}"
      effect = "Allow"
      actions = [
        "elasticloadbalancing:DeleteLoadBalancer",
        "elasticloadbalancing:DeleteTargetGroup",
        "ec2:DeleteVolume",
        "ec2:ReleaseAddress",
        "ec2:DeleteSecurityGroup",
      ]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "aws:ResourceTag/kubernetes.io/cluster/${statement.value}"
        values   = ["owned", "shared"]
      }
    }
  }

  # Terminating instances is held apart from the deletes above, and deliberately
  # excludes var.cluster_name. Everything else on that list is inert once its
  # cluster is gone, so a cluster tag is a sufficient bound; an instance is not
  # -- the live cluster's own nodes, Karpenter's and the managed group's alike,
  # carry `kubernetes.io/cluster/<name>=owned` while serving tenants. Granting
  # terminate on that tag alone would leave `cluster_is_dead_and_ours` in the
  # shell script as the only thing standing between a bug and every running
  # node, which is precisely the arrangement the block above exists to avoid.
  #
  # Nothing is lost by the exclusion: the sweep only terminates instances whose
  # cluster no longer exists, and the cluster it is running in does. Instances
  # stranded by a cluster that was destroyed and rebuilt under the same name are
  # likewise not reclaimable here -- the live cluster answers to that tag -- and
  # are the sweep's ownership rules working as intended. A rebuild under a *new*
  # name adds the old one to var.sweepable_clusters, which is where the orphans
  # of a previous incarnation are meant to be reclaimed from.
  #
  # The second condition narrows it further to Karpenter's own nodes: a managed
  # node group's instances are Terraform's to remove, and the sweep has no
  # business terminating them even in a dead cluster.
  dynamic "statement" {
    for_each = toset(var.sweepable_clusters)
    content {
      sid       = "InfraOrphanSweepTerminateKarpenter${replace(title(replace(statement.value, "-", " ")), " ", "")}"
      effect    = "Allow"
      actions   = ["ec2:TerminateInstances"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "aws:ResourceTag/kubernetes.io/cluster/${statement.value}"
        values   = ["owned", "shared"]
      }
      condition {
        test     = "StringLike"
        variable = "aws:ResourceTag/karpenter.sh/nodepool"
        values   = ["*"]
      }
    }
  }

  # Teardown maintains IRSA trust on the shared per-service roles (add/remove the
  # tenant namespace SA). Scoped to the otterworks-* service roles only.
  statement {
    sid       = "TenantIrsaTrust"
    effect    = "Allow"
    actions   = ["iam:GetRole", "iam:UpdateAssumeRolePolicy"]
    resources = ["arn:aws:iam::${local.account_id}:role/otterworks-*"]
  }

  # deploy/teardown resolve shared RDS/S3/DynamoDB coordinates by reading the
  # application Terraform state (load_infra_outputs -> `terraform output`).
  # Read-only: TF 1.9 S3 backend without a lock table performs no writes here.
  statement {
    sid       = "TerraformStateList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.terraform_state_bucket}"]
  }
  statement {
    sid       = "TerraformStateRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.terraform_state_bucket}/*"]
  }

  # deploy-tenant.sh resolves the newest image tag per service from ECR.
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid    = "EcrResolveTags"
    effect = "Allow"
    actions = [
      "ecr:DescribeImages", "ecr:DescribeRepositories",
      "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
    ]
    resources = ["arn:aws:ecr:${var.aws_region}:${local.account_id}:repository/${var.ecr_repo_prefix}/*"]
  }

  # Reaper orphan sweep enumerates all app tables/buckets to compare against the
  # control table. List* are account-level (no resource ARN).
  statement {
    sid       = "ReaperEnumerate"
    effect    = "Allow"
    actions   = ["dynamodb:ListTables", "s3:ListAllMyBuckets"]
    resources = ["*"]
  }

  # Reaper GC of per-tenant Route53 records (host-based routing). Scoped to
  # hosted-zone record changes; list actions are account-level.
  statement {
    sid    = "ReaperRoute53"
    effect = "Allow"
    actions = [
      "route53:ListHostedZonesByName", "route53:ListHostedZones",
      "route53:ListResourceRecordSets", "route53:ChangeResourceRecordSets",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "dashboard" {
  name   = "control-plane"
  role   = aws_iam_role.dashboard.id
  policy = data.aws_iam_policy_document.dashboard.json
}
