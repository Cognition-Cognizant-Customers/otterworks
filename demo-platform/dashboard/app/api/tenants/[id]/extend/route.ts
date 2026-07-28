import { NextRequest } from "next/server";
import { withSession, json, error } from "@/lib/api";
import { appendAudit, extend, getTenant } from "@/lib/control";
import { NEVER_TTL_SECONDS, ttlToSeconds } from "@/lib/util";
import type { ExtendRequest } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = withSession(async (req: NextRequest, { actor, params }) => {
  const id = params?.id;
  if (!id) return error(400, "missing id");

  const body = (await req.json().catch(() => ({}))) as ExtendRequest;
  const ttlStr = typeof body.ttl === "string" ? body.ttl : "";
  const ttlSeconds = ttlToSeconds(ttlStr);
  if (ttlSeconds === null) return error(400, "invalid ttl");
  // `never` is a decade, and granting it here would leave a tenant the reaper
  // never collects with `persistent` still false -- immortal, and outside the
  // PERPETUAL_TENANT_IDS allowlist that is meant to be the only way there.
  if (ttlSeconds >= NEVER_TTL_SECONDS) {
    return error(400, `ttl '${ttlStr}' is perpetual; POST /api/tenants/${id}/persist to make a tenant perpetual`);
  }

  const tenant = await getTenant(id);
  if (!tenant) return error(404, "not found");
  // Extending a perpetual tenant would *shorten* it: `extend` writes now+ttl,
  // and no ttl a caller can name comes close to a decade.
  if (tenant.persistent) {
    return error(
      409,
      `tenant '${id}' is persistent and does not expire; POST /api/tenants/${id}/persist {"persistent":false} to give it a ttl`,
    );
  }

  const expiresAt = await extend(id, ttlSeconds);
  await appendAudit({ tenantId: id, action: "extend", actor, detail: `ttl=${ttlStr}` });

  return json({ ok: true, expiresAt });
});
