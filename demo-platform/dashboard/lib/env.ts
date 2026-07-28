// Centralised, typed access to runtime configuration. Nothing here reads a
// secret's value into logs; callers only ever compare/sign with them.

export const env = {
  get dashboardPasscode(): string | undefined {
    return process.env.DASHBOARD_PASSCODE;
  },
  get sessionSecret(): string | undefined {
    return process.env.SESSION_SECRET;
  },
  get controlTable(): string {
    return process.env.CONTROL_TABLE || "otterworks-demo-control";
  },
  get awsRegion(): string {
    return process.env.AWS_REGION || "us-east-1";
  },
  get eksCluster(): string {
    return process.env.EKS_CLUSTER || "otterworks-dev";
  },
  get platformNamespace(): string {
    return process.env.PLATFORM_NAMESPACE || "otterworks-platform";
  },
  get runnerImage(): string | undefined {
    return process.env.RUNNER_IMAGE;
  },
  get serviceAccount(): string {
    return process.env.DASHBOARD_SERVICE_ACCOUNT || "demo-ops-dashboard";
  },
  // Secret (K8s) that the runner Job references via env valueFrom — never
  // passed on argv. Its keys hold DB_PASSWORD / AWS creds etc.
  get runnerSecretName(): string {
    return process.env.RUNNER_SECRET_NAME || "demo-ops-dashboard";
  },
  get hostSuffix(): string {
    return process.env.HOST_SUFFIX || "demo.otterworks.app";
  },
  // Tenant ids allowed to be perpetual. A perpetual tenant never expires and is
  // never idle-suspended, so it bills continuously and no reaper pass will ever
  // clean it up: that is a standing cost decision, not something any dashboard
  // caller should be able to make for an arbitrary id. Everything else is TTL'd.
  get perpetualTenantIds(): Set<string> {
    // Unset defaults to `main`; explicitly empty means no id may be perpetual,
    // which fails closed -- a checkout asking for one is refused rather than
    // quietly given an environment that never expires.
    const raw = process.env.PERPETUAL_TENANT_IDS ?? "main";
    return new Set(
      raw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    );
  },
  // The perpetual tenant is the shared reference environment and gets a shorter
  // URL than the per-attendee ones (t-main.otterworks.app, not
  // t-main.demo.otterworks.app). Both are covered by the wildcard certificate.
  get perpetualHostSuffix(): string {
    return process.env.PERPETUAL_HOST_SUFFIX || "otterworks.app";
  },
  // HTTPS clone URL passed to runner Jobs so they can fetch participant branches
  // (workshop-<id>) with GITHUB_TOKEN. Empty -> runner uses the image's bundled
  // tree (golden app) and code-level variants rely on --image-tag instead.
  get repoHttpsUrl(): string {
    return process.env.REPO_HTTPS_URL || "";
  },
  // Services that are crash-looping BY DESIGN on the golden app (planted
  // workshop bugs, e.g. admin-service's Rails logger bug). A tenant whose only
  // unhealthy pods are these is still "active" — otherwise every tenant would
  // perpetually read "error". Override with a comma-separated EXPECTED_DEGRADED_SERVICES.
  get expectedDegradedServices(): Set<string> {
    const raw = process.env.EXPECTED_DEGRADED_SERVICES;
    const list = (raw ?? "admin-service")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    return new Set(list);
  },
  get sessionTtlSeconds(): number {
    const raw = process.env.SESSION_TTL_SECONDS;
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) && n > 0 ? n : 8 * 60 * 60; // ~8h
  },
} as const;

export const TENANT_LABEL = "demo/tenant";
export const TTL_LABEL = "demo/expires-at";
