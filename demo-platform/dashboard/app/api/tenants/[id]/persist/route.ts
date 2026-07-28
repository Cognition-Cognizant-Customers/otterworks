import { NextRequest } from "next/server";
import { withSession, json, error } from "@/lib/api";
import { appendAudit, getTenant, setPersistent } from "@/lib/control";
import { env } from "@/lib/env";
import type { PersistRequest } from "@/lib/types";
import { NEVER_TTL_SECONDS, ttlToSeconds } from "@/lib/util";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// What a tenant gets back when it stops being perpetual. Long enough for
// whoever cleared the flag to finish what they were doing, short enough that
// forgetting to check it in still costs a day rather than forever.
const UNPERSIST_TTL = "24h";

export const POST = withSession(async (req: NextRequest, { actor, params }) => {
  const id = params?.id;
  if (!id) return error(400, "missing id");

  const tenant = await getTenant(id);
  if (!tenant) return error(404, "not found");

  const body = (await req.json().catch(() => ({}))) as PersistRequest & { ttl?: string };
  if (typeof body.persistent !== "boolean") return error(400, "missing persistent (boolean)");

  // Only the ids configured as perpetual may take the flag. Clearing it is
  // always allowed -- that direction only ever returns a tenant to the reaper.
  if (body.persistent && !env.perpetualTenantIds.has(id)) {
    return error(
      403,
      `tenant '${id}' may not be perpetual; a perpetual tenant never expires and is never suspended`,
    );
  }

  // Resolve the new expiry before writing anything, so an unparseable ttl is
  // rejected with the tenant untouched rather than half-changed.
  let ttlSeconds: number;
  if (body.persistent) {
    ttlSeconds = NEVER_TTL_SECONDS;
  } else {
    const ttl = typeof body.ttl === "string" && body.ttl.trim() ? body.ttl.trim() : UNPERSIST_TTL;
    const seconds = ttlToSeconds(ttl);
    if (seconds === null || seconds >= NEVER_TTL_SECONDS) return error(400, "invalid ttl");
    ttlSeconds = seconds;
  }

  const expiresAt = await setPersistent(id, body.persistent, ttlSeconds);

  await appendAudit({
    tenantId: id,
    action: "persist",
    actor,
    detail: body.persistent
      ? "marked perpetual (exempt from reaper and idle-suspend)"
      : `returned to TTL (expires_at=${expiresAt})`,
  });

  return json({ ok: true, persistent: body.persistent, expiresAt });
});
