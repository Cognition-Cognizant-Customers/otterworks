import { NextRequest } from "next/server";
import { withSession, json, error } from "@/lib/api";
import { appendAudit, checkout } from "@/lib/control";
import { createRunnerJob } from "@/lib/jobs";
import { env } from "@/lib/env";
import { isNeverTtl, isValidId, randomIdSuffix, sanitizeId, ttlToSeconds } from "@/lib/util";
import type { CheckoutRequest, TenantTier } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = withSession(async (req: NextRequest, { actor }) => {
  const body = (await req.json().catch(() => ({}))) as CheckoutRequest;

  const rawId = typeof body.id === "string" && body.id.trim() ? body.id : `a${randomIdSuffix()}`;
  const id = sanitizeId(rawId);
  if (!isValidId(id)) return error(400, "invalid tenant id");

  const owner = typeof body.owner === "string" && body.owner.trim() ? body.owner.trim() : actor;
  const branch =
    typeof body.branch === "string" && body.branch.trim() ? body.branch.trim() : `workshop-${id}`;
  const tier: TenantTier = body.tier === "B" ? "B" : "A";
  const imageTag = typeof body.image_tag === "string" && body.image_tag ? body.image_tag : undefined;

  // "never" and persistent:true are the same request; accept either spelling so
  // a caller cannot end up with a tenant it believes is perpetual while the
  // reaper still expires it.
  const persistent = body.persistent === true || isNeverTtl(body.ttl ?? "");
  if (persistent && !env.perpetualTenantIds.has(id)) {
    return error(
      403,
      `tenant '${id}' may not be perpetual; a perpetual tenant never expires and is never suspended`,
    );
  }
  const ttlStr = persistent ? "never" : typeof body.ttl === "string" && body.ttl ? body.ttl : "8h";
  const ttlSeconds = ttlToSeconds(ttlStr);
  if (ttlSeconds === null) return error(400, "invalid ttl");

  // The perpetual tenant is the environment everyone shares, so it answers on
  // the short host (t-main.otterworks.app) rather than under the per-attendee
  // subdomain. The runner must be told the same suffix the record advertises,
  // or the URL in the dashboard points at an Ingress that was never created.
  const hostSuffix = persistent ? env.perpetualHostSuffix : env.hostSuffix;

  const tenant = await checkout({
    id,
    owner,
    branch,
    tier,
    imageTag,
    ttlSeconds,
    hostSuffix,
    persistent,
  });

  await appendAudit({
    tenantId: id,
    action: "checkout",
    actor,
    detail: `branch=${branch} tier=${tier} ttl=${ttlStr}`,
  });

  // Fire the deploy runner Job. If the runner image isn't configured yet the
  // tenant record still exists (status=deploying) and the error surfaces.
  try {
    await createRunnerJob({
      action: "deploy",
      tenantId: id,
      branch,
      tier,
      imageTag,
      ttl: ttlStr,
      hostSuffix,
    });
  } catch (err) {
    await appendAudit({
      tenantId: id,
      action: "deploy_fail",
      actor,
      detail: err instanceof Error ? err.message : "job create failed",
    });
    return json({ tenant, warning: "deploy job not enqueued (runner not configured)" }, 202);
  }

  return json(tenant, 201);
});
