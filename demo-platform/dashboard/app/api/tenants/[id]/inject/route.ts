import { NextRequest } from "next/server";
import { withSession, json, error } from "@/lib/api";
import { appendAudit, getTenant } from "@/lib/control";
import { createRunnerJob } from "@/lib/jobs";
import type { InjectRequest } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = withSession(async (req: NextRequest, { actor, params }) => {
  const id = params?.id;
  if (!id) return error(400, "missing id");

  const body = (await req.json().catch(() => ({}))) as InjectRequest;
  const scenario = typeof body.scenario === "string" && body.scenario.trim() ? body.scenario.trim() : "";
  if (!scenario) return error(400, "missing scenario");

  const tenant = await getTenant(id);
  if (!tenant) return error(404, "not found");

  // The perpetual tenant is everyone's reference environment: breaking it on
  // purpose is exactly what it exists not to do. Bug-hunt labs get their own
  // ephemeral tenant. (`reset` stays allowed -- it only restores.)
  if (tenant.persistent) {
    return error(409, `tenant '${id}' is persistent; inject a bug into an ephemeral tenant instead`);
  }

  await appendAudit({ tenantId: id, action: "inject", actor, detail: `scenario=${scenario}` });
  const jobName = await createRunnerJob({ action: "inject", tenantId: id, scenario });
  return json({ ok: true, job: jobName });
});
