import { NextRequest } from "next/server";
import { withSession, json, error } from "@/lib/api";
import { getReaperConfig, putReaperConfig } from "@/lib/control";
import type { ReaperUpdateRequest } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const GET = withSession(async () => {
  const cfg = await getReaperConfig();
  return json(cfg);
});

export const PUT = withSession(async (req: NextRequest, { actor }) => {
  const body = (await req.json().catch(() => ({}))) as Partial<ReaperUpdateRequest>;

  const scheduleCron = typeof body.schedule_cron === "string" ? body.schedule_cron.trim() : "";
  if (!/^\S+\s+\S+\s+\S+\s+\S+\s+\S+$/.test(scheduleCron)) {
    return error(400, "invalid schedule_cron (expect 5 cron fields)");
  }
  const graceSeconds = Number(body.grace_seconds);
  if (!Number.isFinite(graceSeconds) || graceSeconds < 0) {
    return error(400, "invalid grace_seconds");
  }
  const idleAfterSeconds = Number(body.idle_after_seconds);
  if (!Number.isFinite(idleAfterSeconds) || idleAfterSeconds <= 0) {
    return error(400, "invalid idle_after_seconds");
  }
  const enabled = Boolean(body.enabled);
  const sweepOrphans = Boolean(body.sweep_orphans);
  const suspendIdle = Boolean(body.suspend_idle);
  const sweepInfra = Boolean(body.sweep_infra);
  const sweepInfraDelete = Boolean(body.sweep_infra_delete);

  const cfg = await putReaperConfig(
    {
      scheduleCron,
      graceSeconds,
      enabled,
      sweepOrphans,
      suspendIdle,
      idleAfterSeconds,
      sweepInfra,
      sweepInfraDelete,
    },
    actor,
  );
  return json(cfg);
});
