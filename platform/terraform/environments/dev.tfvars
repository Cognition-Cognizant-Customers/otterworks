aws_region   = "us-east-1"
environment  = "dev"
cluster_name = "otterworks-dev"

# MUST stay within EKS standard support. Extended support costs $0.60/hr against
# a standard rate of $0.10/hr -- a $360/month penalty that buys nothing, which
# is exactly what running 1.32 past its window was costing. 1.34 holds standard
# support until 2026-12-02; schedule the next bump before then.
cluster_version = "1.34"

vpc_cidr           = "10.0.0.0/16"
az_count           = 2
enable_nat_gateway = false

# This group is the SYSTEM pool, not the tenant pool. Tenant capacity comes and
# goes with Karpenter (demo-platform/scripts/install-karpenter.sh), which the
# managed node group cannot do: its size is a fixed number, so it neither grows
# for pending tenant pods nor gives anything back when the reaper scales an idle
# tenant to zero -- the node it emptied keeps billing.
#
# What stays here is the platform itself: the Karpenter controller (which needs
# somewhere to run that Karpenter does not own), ingress-nginx, cert-manager,
# external-dns, CoreDNS and the ops dashboard. It is deliberately no longer the
# thing that scales.
#
# One node, not two. Two bought AZ redundancy for the platform pods, which a
# demo environment does not need and pays for around the clock -- a second
# always-on Spot xlarge is most of what is left of the floor once the tenants
# themselves cost nothing while idle. The tradeoff is explicit: lose this node
# and ingress is down until the group replaces it (a few minutes), rather than
# failing over. Everything that has to survive a node going away already lives
# outside the cluster -- the control table in DynamoDB, tenant data in RDS.
#
# Keeping it at one rather than zero is what avoids a cold start: the platform
# has to be up to receive the checkout that creates a tenant, and Karpenter has
# to be running somewhere before it can launch anything. The ceiling stays above
# one so a rollout or a node replacement has somewhere to go.
#
# SPOT keeps the rate ~70% below on-demand; xlarge because a few large nodes
# bin-pack many small pods far better than many small ones.
node_instance_types = ["m6a.xlarge", "m6i.xlarge", "m5.xlarge", "t3.xlarge"]
node_capacity_type  = "SPOT"
node_desired_size   = 1
node_min_size       = 1
node_max_size       = 3

ecr_prefix = "otterworks/"
