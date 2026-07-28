import { NextRequest } from "next/server";
import { withSession, json, error } from "@/lib/api";
import { appendAudit, getTenant } from "@/lib/control";
import { activeRunnerJob, createRunnerJob } from "@/lib/jobs";
import { env } from "@/lib/env";
import { secondsToTtl } from "@/lib/util";
import type { RedeployRequest } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Ship the tenant's branch into the environment it already owns.
 *
 * This is the CD entry point, and the deliberate difference from checkout is
 * that it changes nothing about the reservation: same owner, same branch, and
 * the same expiry the tenant already had. A push must never quietly extend an
 * environment's life, or nothing would ever be reaped while anyone was working.
 */
export const POST = withSession(async (req: NextRequest, { actor, params }) => {
  const id = params?.id;
  if (!id) return error(400, "missing id");

  const tenant = await getTenant(id);
  if (!tenant) return error(404, "not found");
  if (tenant.status === "draining" || tenant.status === "free") {
    return error(409, `tenant '${id}' is ${tenant.status}; check it out before redeploying`);
  }

  const body = (await req.json().catch(() => ({}))) as RedeployRequest;
  const imageTag =
    typeof body.image_tag === "string" && body.image_tag.trim() ? body.image_tag.trim() : undefined;
  const requestedBranch =
    typeof body.branch === "string" && body.branch.trim() ? body.branch.trim() : undefined;

  // A tenant belongs to one branch. Both `workshop-derek` and `demo-derek` map
  // to tenant `derek`, so without this the second branch to be pushed would
  // silently overwrite the first branch's environment.
  if (requestedBranch && requestedBranch !== tenant.branch) {
    return error(
      409,
      `tenant '${id}' is deployed from '${tenant.branch ?? "(none)"}', not '${requestedBranch}'`,
    );
  }

  // Not caught: if the cluster cannot be listed we do not know whether a deploy
  // is already running, and guessing "no" is how a tenant gets two concurrent
  // `helm upgrade`s.
  const inFlight = await activeRunnerJob(id, "deploy");
  if (inFlight) return error(409, `deploy already running for '${id}' (${inFlight})`);

  // Keep whatever lifetime is left. The floor matters because the runner writes
  // now+TTL on success: a tenant redeployed in its last seconds would otherwise
  // come back already expired and be reaped mid-deploy.
  const remaining = Math.max(300, tenant.expiresAt - Math.floor(Date.now() / 1000));
  const ttl = tenant.persistent ? "never" : secondsToTtl(remaining);

  let jobName: string;
  try {
    jobName = await createRunnerJob({
      action: "deploy",
      tenantId: id,
      branch: tenant.branch,
      tier: tenant.tier,
      imageTag,
      ttl,
      hostSuffix: hostSuffixOf(tenant.url) ?? env.hostSuffix,
      redeploy: true,
    });
  } catch (err) {
    await appendAudit({
      tenantId: id,
      action: "deploy_fail",
      actor,
      detail: err instanceof Error ? err.message : "job create failed",
    });
    throw err;
  }

  await appendAudit({
    tenantId: id,
    action: "redeploy",
    actor,
    detail: `branch=${tenant.branch ?? "-"} image_tag=${imageTag ?? "-"} job=${jobName}`,
  });

  return json({ ok: true, job: jobName, branch: tenant.branch, imageTag, ttl });
});

// The tenant's own host, not the current default: the perpetual tenant lives on
// a different suffix, and a redeploy that changed it would move the URL out
// from under whoever is using it.
function hostSuffixOf(url?: string): string | undefined {
  if (!url) return undefined;
  try {
    const host = new URL(url).hostname;
    const dot = host.indexOf(".");
    return dot === -1 ? undefined : host.slice(dot + 1);
  } catch {
    return undefined;
  }
}
