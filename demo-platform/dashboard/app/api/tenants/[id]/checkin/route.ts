import { withSession, json, error } from "@/lib/api";
import { appendAudit, checkin, getTenant } from "@/lib/control";
import { createRunnerJob } from "@/lib/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = withSession(async (_req, { actor, params }) => {
  const id = params?.id;
  if (!id) return error(400, "missing id");

  const tenant = await getTenant(id);
  if (!tenant) return error(404, "not found");

  // A perpetual tenant is shared infrastructure: check-in destroys its
  // namespace and database. There is deliberately no force flag -- clearing
  // `persistent` is a separate, audited call, so no single request (or
  // mistyped id) can tear one down.
  if (tenant.persistent) {
    return error(
      409,
      `tenant '${id}' is persistent; POST /api/tenants/${id}/persist {"persistent":false} first`,
    );
  }

  await checkin(id);
  await appendAudit({ tenantId: id, action: "checkin", actor });

  try {
    await createRunnerJob({ action: "teardown", tenantId: id });
  } catch (err) {
    await appendAudit({
      tenantId: id,
      action: "checkin",
      actor,
      detail: err instanceof Error ? err.message : "teardown job create failed",
    });
    return json({ ok: true, warning: "teardown job not enqueued (runner not configured)" }, 202);
  }

  return json({ ok: true, status: "draining" });
});
