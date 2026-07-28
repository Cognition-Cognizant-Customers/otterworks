# ------------------------------------------------------------------------------
# OtterWorks Platform - VPC Module
# Standalone VPC with public and private subnets for EKS
# ------------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  common_tags = {
    Module  = "vpc"
    Project = var.project
  }

  # Karpenter's EC2NodeClass finds where to launch nodes by this tag, so it goes
  # on the subnets nodes actually belong in -- private when there is a NAT
  # gateway to reach the internet through, public otherwise, matching what the
  # root module hands the node group. Tagging both would let Karpenter place a
  # node in a private subnet with no route out, where it would fail to pull
  # images or register with the cluster.
  #
  # It lives in the subnet's own tag set rather than in a separate aws_ec2_tag
  # resource because aws_subnet owns the whole set and strips anything else on
  # the next apply.
  karpenter_discovery = { "karpenter.sh/discovery" = var.cluster_name }

  karpenter_discovery_public  = var.enable_nat_gateway ? {} : local.karpenter_discovery
  karpenter_discovery_private = var.enable_nat_gateway ? local.karpenter_discovery : {}
}

# --- VPC ---

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}"
  })
}

# --- Internet Gateway ---

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}"
  })
}

# --- Public Subnets ---

resource "aws_subnet" "public" { # nosemgrep: terraform.aws.security.aws-subnet-has-public-ip-address.aws-subnet-has-public-ip-address
  count = var.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 100 + count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, local.karpenter_discovery_public, {
    Name                                        = "${var.project}-public-${local.azs[count.index]}"
    "kubernetes.io/role/elb"                    = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  })
}

# --- Private Subnets ---

resource "aws_subnet" "private" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone = local.azs[count.index]

  tags = merge(local.common_tags, local.karpenter_discovery_private, {
    Name                                        = "${var.project}-private-${local.azs[count.index]}"
    "kubernetes.io/role/internal-elb"           = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  })
}

# --- NAT Gateway (single, cost-optimized for dev) ---

resource "aws_eip" "nat" {
  count  = var.enable_nat_gateway ? 1 : 0
  domain = "vpc"

  tags = merge(local.common_tags, {
    Name = "${var.project}-nat-${var.environment}"
  })
}

resource "aws_nat_gateway" "main" {
  count = var.enable_nat_gateway ? 1 : 0

  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = merge(local.common_tags, {
    Name = "${var.project}-nat-${var.environment}"
  })

  depends_on = [aws_internet_gateway.main]
}

# --- Route Tables ---

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(local.common_tags, {
    Name = "${var.project}-public-${var.environment}"
  })
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  dynamic "route" {
    for_each = var.enable_nat_gateway ? [1] : []
    content {
      cidr_block     = "0.0.0.0/0"
      nat_gateway_id = aws_nat_gateway.main[0].id
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.project}-private-${var.environment}"
  })
}

resource "aws_route_table_association" "private" {
  count = var.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
