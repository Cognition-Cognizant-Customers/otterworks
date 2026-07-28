# ------------------------------------------------------------------------------
# OtterWorks Demo Platform — runner image (otterworks/demo-runner)
#
# Launched as a Kubernetes Job by the ops dashboard to run ONE tenant op
# (deploy / teardown / inject / reset / reap). Carries the repo + a full CLI
# toolchain so it can drive the EXISTING scripts in scripts/.
#
# Build from the REPO ROOT so the whole repo (scripts/ + demo-platform/) is in
# the build context:
#   docker build -f demo-platform/runner/Dockerfile \
#     -t <registry>/otterworks/demo-runner:<tag> .
# ------------------------------------------------------------------------------
FROM alpine:3.20

# Pinned tool versions (arch-aware). aws-cli v2 + jq + git + psql come from apk;
# kubectl/helm/terraform are installed from upstream release binaries so the
# versions are explicit and reproducible regardless of apk channel churn.
ARG KUBECTL_VERSION=1.30.4
ARG HELM_VERSION=3.15.4
ARG TERRAFORM_VERSION=1.9.5
ARG TARGETARCH=amd64

# aws-cli in Alpine community is v2 (built for musl). bash/git/jq/postgresql-client
# round out the toolchain the scripts need; ca-certificates/curl for downloads.
RUN apk add --no-cache \
      bash \
      git \
      jq \
      curl \
      tar \
      unzip \
      ca-certificates \
      openssl \
      coreutils \
      aws-cli \
      postgresql-client \
  && aws --version

# kubectl
RUN curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
  && chmod +x /usr/local/bin/kubectl \
  && kubectl version --client=true

# helm
RUN curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" \
      | tar -xz -C /tmp \
  && mv "/tmp/linux-${TARGETARCH}/helm" /usr/local/bin/helm \
  && rm -rf "/tmp/linux-${TARGETARCH}" \
  && chmod +x /usr/local/bin/helm \
  && helm version

# terraform
RUN curl -fsSL -o /tmp/terraform.zip \
      "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" \
  && unzip -o /tmp/terraform.zip -d /usr/local/bin \
  && rm -f /tmp/terraform.zip \
  && chmod +x /usr/local/bin/terraform \
  && terraform version

# The repo (scripts/ + demo-platform/) — the runner checks out the requested
# branch at runtime via entrypoint.sh (TENANT_BRANCH).
WORKDIR /workspace
COPY . /workspace

RUN chmod +x \
      /workspace/demo-platform/runner/entrypoint.sh \
      /workspace/demo-platform/reaper/reaper.sh \
      /workspace/scripts/*.sh \
  && git config --system --add safe.directory /workspace

# Run as a non-root user. The runner only needs to read/write /workspace and its
# own $HOME (git/aws/kube config); it never needs root. In-cluster it uses the
# pod ServiceAccount token, which is readable by any uid.
RUN addgroup -S runner \
  && adduser -S -G runner -h /home/runner runner \
  && chown -R runner:runner /workspace /home/runner
USER runner

ENV HOME=/home/runner \
    REPO_DIR=/workspace \
    CONTROL_TABLE=otterworks-demo-control \
    AWS_REGION=us-east-1 \
    EKS_CLUSTER=otterworks-dev \
    HOST_SUFFIX=demo.otterworks.app

ENTRYPOINT ["/workspace/demo-platform/runner/entrypoint.sh"]
