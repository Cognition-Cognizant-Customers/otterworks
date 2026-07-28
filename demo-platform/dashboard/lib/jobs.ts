import * as k8s from "@kubernetes/client-node";
import { batch } from "@/lib/k8s";
import { env } from "@/lib/env";

export type RunnerAction = "deploy" | "teardown" | "inject";

export interface RunnerJobInput {
  action: RunnerAction;
  tenantId: string;
  branch?: string;
  tier?: string;
  imageTag?: string;
  ttl?: string;
  scenario?: string;
  hostSuffix?: string;
  // Deploy into a tenant that is already up (continuous delivery) rather than
  // standing a new one up. Only changes how the runner records the operation.
  redeploy?: boolean;
}

// Secret keys the runner needs at runtime. These are referenced via
// envFrom/valueFrom (never placed on argv) so the passcode / DB password / AWS
// creds are not exposed in the Job spec's command line.
const RUNNER_SECRET_ENV_KEYS = [
  "DB_PASSWORD",
  "JWT_SECRET",
  "SECRET_KEY_BASE",
  "GITHUB_TOKEN",
] as const;

function jobName(action: RunnerAction, id: string, epoch: number): string {
  // Contract: deploy-<id>-<epoch> / teardown-<id>-<epoch>; inject uses inject-bug.
  const prefix = action === "inject" ? "inject-bug" : action;
  return `${prefix}-${id}-${epoch}`;
}

export function jobNamePrefixes(id: string): string[] {
  return [`deploy-${id}-`, `teardown-${id}-`, `inject-bug-${id}-`];
}

function buildEnv(input: RunnerJobInput): k8s.V1EnvVar[] {
  // The runner image ENTRYPOINT (entrypoint.sh) dispatches on OP and does the
  // branch checkout + control-table status/audit writes. We drive it purely
  // through env — never by overriding the container command — so that plumbing
  // always runs. Env names must match the entrypoint contract exactly.
  const plain: k8s.V1EnvVar[] = [
    { name: "OP", value: input.action },
    { name: "CONTROL_TABLE", value: env.controlTable },
    { name: "AWS_REGION", value: env.awsRegion },
    { name: "EKS_CLUSTER", value: env.eksCluster },
    { name: "HOST_SUFFIX", value: input.hostSuffix ?? env.hostSuffix },
    { name: "TENANT_ID", value: input.tenantId },
    { name: "ACTOR", value: "dashboard" },
  ];
  if (input.branch) plain.push({ name: "TENANT_BRANCH", value: input.branch });
  if (env.repoHttpsUrl) plain.push({ name: "REPO_HTTPS_URL", value: env.repoHttpsUrl });
  if (input.tier) plain.push({ name: "TIER", value: input.tier });
  if (input.imageTag) plain.push({ name: "IMAGE_TAG", value: input.imageTag });
  if (input.ttl) plain.push({ name: "TTL", value: input.ttl });
  if (input.scenario) plain.push({ name: "SCENARIO", value: input.scenario });
  if (input.redeploy) plain.push({ name: "REDEPLOY", value: "true" });

  // Secrets injected by reference — values never appear in the Job manifest.
  const secretEnv: k8s.V1EnvVar[] = RUNNER_SECRET_ENV_KEYS.map((key) => ({
    name: key,
    valueFrom: {
      secretKeyRef: { name: env.runnerSecretName, key, optional: true },
    },
  }));

  return [...plain, ...secretEnv];
}

/** Build the Job manifest (pure — testable without a cluster). */
export function buildRunnerJob(input: RunnerJobInput, epoch: number): k8s.V1Job {
  const image = env.runnerImage;
  if (!image) throw new Error("RUNNER_IMAGE is not configured");
  const name = jobName(input.action, input.tenantId, epoch);

  return {
    apiVersion: "batch/v1",
    kind: "Job",
    metadata: {
      name,
      namespace: env.platformNamespace,
      labels: {
        "app.kubernetes.io/managed-by": "demo-ops-dashboard",
        "demo/action": input.action,
        "demo/tenant-id": input.tenantId,
      },
    },
    spec: {
      backoffLimit: 1,
      ttlSecondsAfterFinished: 3600,
      template: {
        metadata: {
          labels: {
            "app.kubernetes.io/managed-by": "demo-ops-dashboard",
            "demo/action": input.action,
          },
        },
        spec: {
          serviceAccountName: env.serviceAccount,
          restartPolicy: "Never",
          containers: [
            {
              name: "runner",
              image,
              // No command override: the image ENTRYPOINT (entrypoint.sh) runs
              // and dispatches on the OP env var set in buildEnv().
              env: buildEnv(input),
            },
          ],
        },
      },
    },
  };
}

/**
 * Name of an unfinished Job of this action for this tenant, if any.
 *
 * Two overlapping deploys of one tenant are two `helm upgrade` runs against
 * the same releases, which is how you get a half-applied environment. CD makes
 * that easy to hit (two pushes in a minute), so callers check this first.
 */
export async function activeRunnerJob(
  tenantId: string,
  action: RunnerAction,
): Promise<string | null> {
  const res = await batch().listNamespacedJob(
    env.platformNamespace,
    undefined,
    undefined,
    undefined,
    undefined,
    `demo/tenant-id=${tenantId},demo/action=${action}`,
  );
  const running = res.body.items.find(
    (j) => (j.status?.active ?? 0) > 0 || (!j.status?.succeeded && !j.status?.failed),
  );
  return running?.metadata?.name ?? null;
}

/** Create the runner Job in the platform namespace. Returns the Job name. */
export async function createRunnerJob(input: RunnerJobInput): Promise<string> {
  const epoch = Math.floor(Date.now() / 1000);
  const job = buildRunnerJob(input, epoch);
  await batch().createNamespacedJob(env.platformNamespace, job);
  return job.metadata?.name ?? jobName(input.action, input.tenantId, epoch);
}
