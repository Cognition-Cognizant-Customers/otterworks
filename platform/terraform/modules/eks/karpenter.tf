# ------------------------------------------------------------------------------
# Karpenter - node autoscaling and consolidation
#
# The managed node group cannot scale itself: nothing watches for Pending pods,
# so capacity past its desired size never arrives and tenant pods simply sit
# unschedulable. It also has no way to give capacity back, which is what makes
# the idle scale-to-zero in the reaper only half a cost control -- the tenant's
# pods go away and the node they were on keeps billing.
#
# Karpenter closes both halves: it launches nodes shaped to the pods that are
# actually pending, and consolidates or removes them as pods go away. This file
# provisions only what has to exist in AWS (controller role, node instance
# profile, interruption queue, discovery tags); the controller and its NodePool
# are installed by demo-platform/scripts/install-karpenter.sh.
#
# Nodes reuse the managed node group's role, so they are already mapped in the
# aws-auth ConfigMap this cluster authenticates with and join without a second
# mapping to keep in step.
# ------------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  karpenter_enabled     = var.enable_karpenter ? 1 : 0
  karpenter_oidc_issuer = replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")
  karpenter_node_tag    = "aws:ResourceTag/kubernetes.io/cluster/${var.cluster_name}"
}

# --- Discovery tag ---
#
# The EC2NodeClass finds the security group and subnets to launch into by tag
# rather than by hardcoded id, so a rebuilt VPC needs no manifest change. The
# subnet half is tagged by the VPC module, which owns those tag sets; this one
# is a standalone tag because the cluster security group is created by EKS, not
# by Terraform.

resource "aws_ec2_tag" "karpenter_sg_discovery" {
  count = local.karpenter_enabled

  resource_id = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  key         = "karpenter.sh/discovery"
  value       = var.cluster_name
}

# --- Node instance profile ---
#
# Created here rather than letting Karpenter manage it, so the controller needs
# no IAM write permissions at all.

resource "aws_iam_instance_profile" "karpenter_node" {
  count = local.karpenter_enabled

  name = "${var.project}-karpenter-node-${var.environment}"
  role = aws_iam_role.node_group.name

  tags = merge(local.common_tags, {
    Component = "karpenter"
  })
}

# --- Interruption queue ---
#
# Every node here is Spot. Without this queue the first warning of a reclaim is
# the node disappearing; with it Karpenter gets the two-minute notice, cordons
# and drains, and has replacement capacity coming before the pods are evicted.

resource "aws_sqs_queue" "karpenter" {
  count = local.karpenter_enabled

  name                      = "${var.project}-karpenter-${var.environment}"
  message_retention_seconds = 300
  sqs_managed_sse_enabled   = true

  tags = merge(local.common_tags, {
    Component = "karpenter"
  })
}

data "aws_iam_policy_document" "karpenter_queue" {
  count = local.karpenter_enabled

  statement {
    sid       = "EventBridgeToKarpenterQueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.karpenter[0].arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "sqs.amazonaws.com"]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.karpenter[0].arn]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sqs_queue_policy" "karpenter" {
  count = local.karpenter_enabled

  queue_url = aws_sqs_queue.karpenter[0].url
  policy    = data.aws_iam_policy_document.karpenter_queue[0].json
}

# Spot reclaim, rebalance advice, scheduled maintenance, and terminations made
# outside Karpenter -- all of which leave a node it must stop scheduling to.
locals {
  karpenter_events = {
    spot_interruption = {
      source      = "aws.ec2"
      detail_type = "EC2 Spot Instance Interruption Warning"
    }
    rebalance = {
      source      = "aws.ec2"
      detail_type = "EC2 Instance Rebalance Recommendation"
    }
    instance_state = {
      source      = "aws.ec2"
      detail_type = "EC2 Instance State-change Notification"
    }
    scheduled_change = {
      source      = "aws.health"
      detail_type = "AWS Health Event"
    }
  }
}

resource "aws_cloudwatch_event_rule" "karpenter" {
  for_each = var.enable_karpenter ? local.karpenter_events : {}

  name = "${var.project}-karpenter-${each.key}-${var.environment}"
  event_pattern = jsonencode({
    source        = [each.value.source]
    "detail-type" = [each.value.detail_type]
  })

  tags = merge(local.common_tags, {
    Component = "karpenter"
  })
}

resource "aws_cloudwatch_event_target" "karpenter" {
  for_each = var.enable_karpenter ? local.karpenter_events : {}

  rule      = aws_cloudwatch_event_rule.karpenter[each.key].name
  target_id = "KarpenterInterruptionQueue"
  arn       = aws_sqs_queue.karpenter[0].arn
}

# --- Controller role (IRSA) ---

data "aws_iam_policy_document" "karpenter_controller_assume_role" {
  count = local.karpenter_enabled

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.karpenter_oidc_issuer}:sub"
      values   = ["system:serviceaccount:kube-system:karpenter"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.karpenter_oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "karpenter_controller" {
  count = local.karpenter_enabled

  name               = "${var.project}-karpenter-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.karpenter_controller_assume_role[0].json

  tags = merge(local.common_tags, {
    Component = "karpenter"
  })
}

# Scoped after Karpenter's own recommended policy. The shape that matters: the
# controller may create instances only when they carry this cluster's tags, and
# may terminate only instances that already do -- so a bug in the controller, or
# anything that gets hold of its credentials, cannot reach the rest of the
# account. Untagged and other-cluster instances are outside the grant entirely.
data "aws_iam_policy_document" "karpenter_controller" {
  count = local.karpenter_enabled

  statement {
    sid    = "AllowScopedEC2InstanceAccessActions"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:CreateFleet",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}::image/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}::snapshot/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:security-group/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:subnet/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:capacity-reservation/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:placement-group/*",
    ]
  }

  statement {
    sid    = "AllowScopedEC2LaunchTemplateAccessActions"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:CreateFleet",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:launch-template/*"]

    condition {
      test     = "StringEquals"
      variable = local.karpenter_node_tag
      values   = ["owned"]
    }

    condition {
      test     = "StringLike"
      variable = "aws:ResourceTag/karpenter.sh/nodepool"
      values   = ["*"]
    }
  }

  statement {
    sid    = "AllowScopedEC2InstanceActionsWithTags"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:CreateFleet",
      "ec2:CreateLaunchTemplate",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:fleet/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:instance/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:volume/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:network-interface/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:launch-template/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:spot-instances-request/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes.io/cluster/${var.cluster_name}"
      values   = ["owned"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/eks:eks-cluster-name"
      values   = [var.cluster_name]
    }

    condition {
      test     = "StringLike"
      variable = "aws:RequestTag/karpenter.sh/nodepool"
      values   = ["*"]
    }
  }

  statement {
    sid     = "AllowScopedResourceCreationTagging"
    effect  = "Allow"
    actions = ["ec2:CreateTags"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:fleet/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:instance/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:volume/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:network-interface/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:launch-template/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:spot-instances-request/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes.io/cluster/${var.cluster_name}"
      values   = ["owned"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/eks:eks-cluster-name"
      values   = [var.cluster_name]
    }

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["RunInstances", "CreateFleet", "CreateLaunchTemplate"]
    }

    condition {
      test     = "StringLike"
      variable = "aws:RequestTag/karpenter.sh/nodepool"
      values   = ["*"]
    }
  }

  # Karpenter marks a node it has decided to remove before it removes it.
  statement {
    sid       = "AllowScopedResourceTagging"
    effect    = "Allow"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:instance/*"]

    condition {
      test     = "StringEquals"
      variable = local.karpenter_node_tag
      values   = ["owned"]
    }

    condition {
      test     = "StringLike"
      variable = "aws:ResourceTag/karpenter.sh/nodepool"
      values   = ["*"]
    }

    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:RequestTag/eks:eks-cluster-name"
      values   = [var.cluster_name]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values = [
        "eks:eks-cluster-name",
        "karpenter.sh/nodeclaim",
        "Name",
      ]
    }
  }

  statement {
    sid    = "AllowScopedDeletion"
    effect = "Allow"
    actions = [
      "ec2:TerminateInstances",
      "ec2:DeleteLaunchTemplate",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:instance/*",
      "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:launch-template/*",
    ]

    condition {
      test     = "StringEquals"
      variable = local.karpenter_node_tag
      values   = ["owned"]
    }

    condition {
      test     = "StringLike"
      variable = "aws:ResourceTag/karpenter.sh/nodepool"
      values   = ["*"]
    }
  }

  statement {
    sid    = "AllowRegionalReadActions"
    effect = "Allow"
    actions = [
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeCapacityReservations",
      "ec2:DescribeImages",
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
      "ec2:DescribeInstanceTypeOfferings",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeLaunchTemplates",
      "ec2:DescribePlacementGroups",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSpotPriceHistory",
      "ec2:DescribeSubnets",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [data.aws_region.current.name]
    }
  }

  # AMI ids for the node image family.
  statement {
    sid       = "AllowSSMReadActions"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.name}::parameter/aws/service/*"]
  }

  # Instance type costs, which is how Karpenter picks the cheapest shape that
  # fits the pending pods.
  statement {
    sid       = "AllowPricingReadActions"
    effect    = "Allow"
    actions   = ["pricing:GetProducts"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowInterruptionQueueActions"
    effect = "Allow"
    actions = [
      "sqs:DeleteMessage",
      "sqs:GetQueueUrl",
      "sqs:ReceiveMessage",
    ]
    resources = [aws_sqs_queue.karpenter[0].arn]
  }

  statement {
    sid       = "AllowPassingInstanceRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.node_group.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }

  # Read-only, and needed before the first node exists: the bootstrap script
  # takes the API endpoint and CA from here.
  statement {
    sid       = "AllowAPIServerEndpointDiscovery"
    effect    = "Allow"
    actions   = ["eks:DescribeCluster"]
    resources = ["arn:${data.aws_partition.current.partition}:eks:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:cluster/${var.cluster_name}"]
  }

  # The instance profile is created by Terraform above, so the controller only
  # ever reads it. The list is read-only and cannot be scoped to a resource;
  # without it Karpenter's instance-profile garbage collector fails every pass
  # with AccessDenied, burying real errors in the log.
  statement {
    sid       = "AllowInstanceProfileReadActions"
    effect    = "Allow"
    actions   = ["iam:GetInstanceProfile"]
    resources = [aws_iam_instance_profile.karpenter_node[0].arn]
  }

  statement {
    sid       = "AllowUnscopedInstanceProfileListAction"
    effect    = "Allow"
    actions   = ["iam:ListInstanceProfiles"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "karpenter_controller" {
  count = local.karpenter_enabled

  name   = "${var.project}-karpenter-${var.environment}"
  role   = aws_iam_role.karpenter_controller[0].id
  policy = data.aws_iam_policy_document.karpenter_controller[0].json
}
