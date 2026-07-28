# DNS/TLS is gated behind enable_dns until the domain is registered in Route53.
# Once it exists, `terraform apply -var enable_dns=true` grants an IRSA role that
# external-dns + cert-manager (DNS-01) use to manage records for the tenant
# wildcard.
#
# NOTE: domain *registration* (route53domains register-domain) is a manual,
# contact-info + ICANN-verification step done out of band.
#
# The zone is looked up, never managed. Registering a domain creates its hosted
# zone, so it already exists by the time this runs -- and it has to outlive every
# rebuild of this platform, because it holds the registrar's NS delegation.
# Managing it here meant a plain `terraform apply` (without -var enable_dns=true)
# planned to destroy it, and the blank-slate `terraform destroy` would have taken
# the domain's DNS with it; recreating a zone issues new nameservers, so the
# domain would stay dark until the registrar was updated by hand. Route53's
# refusal to delete a non-empty zone is what stopped that, which is not a control
# worth relying on.
data "aws_route53_zone" "demo" {
  count = var.enable_dns ? 1 : 0
  name  = var.dns_zone_name
}

data "aws_iam_policy_document" "dns_trust" {
  count = var.enable_dns ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringLike"
      variable = "${local.oidc_url}:sub"
      values = [
        "system:serviceaccount:external-dns:external-dns",
        "system:serviceaccount:cert-manager:cert-manager",
      ]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dns" {
  count              = var.enable_dns ? 1 : 0
  name               = "otterworks-demo-dns-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.dns_trust[0].json
}

data "aws_iam_policy_document" "dns" {
  count = var.enable_dns ? 1 : 0
  statement {
    effect    = "Allow"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${data.aws_route53_zone.demo[0].zone_id}"]
  }
  statement {
    effect    = "Allow"
    actions   = ["route53:ListHostedZones", "route53:ListResourceRecordSets", "route53:GetChange"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "dns" {
  count  = var.enable_dns ? 1 : 0
  name   = "dns-automation"
  role   = aws_iam_role.dns[0].id
  policy = data.aws_iam_policy_document.dns[0].json
}
