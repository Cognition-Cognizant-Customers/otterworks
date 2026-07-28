import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  DeleteCommand,
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  QueryCommand,
  ScanCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";
import { env } from "@/lib/env";
import type {
  AuditAction,
  AuditEvent,
  ReaperConfig,
  Tenant,
  TenantStatus,
  TenantTier,
} from "@/lib/types";

// PK/SK layout — exactly as control-table-schema.md.
const pkTenant = (id: string) => `TENANT#${id}`;
const SK_META = "META";
const pkLock = (id: string) => `LOCK#${id}`;
const SK_LOCK = "LOCK";
const PK_REAPER = "CONFIG#reaper";
const SK_CONFIG = "CONFIG";
const pkAudit = (id: string) => `AUDIT#${id}`;

// Raw DynamoDB item shape (snake_case attributes per the schema).
interface TenantItem {
  PK: string;
  SK: string;
  id: string;
  status: TenantStatus;
  owner?: string;
  branch?: string;
  tier: TenantTier;
  image_tag?: string;
  url?: string;
  api_url?: string;
  db_name: string;
  namespace: string;
  created_at: number;
  checked_out_at?: number;
  expires_at: number;
  last_seen_at: number;
  note?: string;
  persistent?: boolean;
}

let _doc: DynamoDBDocumentClient | null = null;

// Lazily construct the client so that `next build` (which imports route
// modules) never needs live AWS credentials.
function doc(): DynamoDBDocumentClient {
  if (!_doc) {
    const base = new DynamoDBClient({ region: env.awsRegion });
    _doc = DynamoDBDocumentClient.from(base, {
      marshallOptions: { removeUndefinedValues: true },
    });
  }
  return _doc;
}

function table(): string {
  return env.controlTable;
}

function itemToTenant(item: TenantItem): Tenant {
  return {
    id: item.id,
    status: item.status,
    owner: item.owner,
    branch: item.branch,
    tier: item.tier,
    imageTag: item.image_tag,
    url: item.url,
    apiUrl: item.api_url,
    dbName: item.db_name,
    namespace: item.namespace,
    createdAt: item.created_at,
    expiresAt: item.expires_at,
    lastSeenAt: item.last_seen_at,
    note: item.note,
    persistent: item.persistent === true,
  };
}

export async function listTenants(): Promise<Tenant[]> {
  const items: TenantItem[] = [];
  let lastKey: Record<string, unknown> | undefined;
  do {
    const res = await doc().send(
      new ScanCommand({
        TableName: table(),
        FilterExpression: "begins_with(PK, :p) AND SK = :meta",
        ExpressionAttributeValues: { ":p": "TENANT#", ":meta": SK_META },
        ExclusiveStartKey: lastKey,
      }),
    );
    for (const it of (res.Items ?? []) as TenantItem[]) items.push(it);
    lastKey = res.LastEvaluatedKey as Record<string, unknown> | undefined;
  } while (lastKey);
  return items.map(itemToTenant);
}

export async function getTenant(id: string): Promise<Tenant | null> {
  const res = await doc().send(
    new GetCommand({ TableName: table(), Key: { PK: pkTenant(id), SK: SK_META } }),
  );
  return res.Item ? itemToTenant(res.Item as TenantItem) : null;
}

export class LockConflictError extends Error {
  constructor(id: string) {
    super(`tenant ${id} is already checked out`);
    this.name = "LockConflictError";
  }
}

export interface CheckoutInput {
  id: string;
  owner: string;
  branch: string;
  tier: TenantTier;
  imageTag?: string;
  ttlSeconds: number;
  hostSuffix: string;
  lockTtlSeconds?: number;
  persistent?: boolean;
}

/**
 * Atomic checkout: conditional PutItem on the LOCK# item (fails if the lock
 * already exists), then upsert the TENANT#/META record with status=deploying.
 */
export async function checkout(input: CheckoutInput): Promise<Tenant> {
  const now = Math.floor(Date.now() / 1000);
  const lockTtl = now + (input.lockTtlSeconds ?? 15 * 60);

  try {
    await doc().send(
      new PutCommand({
        TableName: table(),
        Item: {
          PK: pkLock(input.id),
          SK: SK_LOCK,
          owner: input.owner,
          acquired_at: now,
          lock_ttl: lockTtl,
          ttl: lockTtl,
        },
        ConditionExpression: "attribute_not_exists(PK)",
      }),
    );
  } catch (err) {
    if (err instanceof Error && err.name === "ConditionalCheckFailedException") {
      throw new LockConflictError(input.id);
    }
    throw err;
  }

  const expiresAt = now + input.ttlSeconds;
  const item: TenantItem = {
    PK: pkTenant(input.id),
    SK: SK_META,
    id: input.id,
    status: "deploying",
    owner: input.owner,
    branch: input.branch,
    tier: input.tier,
    image_tag: input.imageTag,
    url: `https://t-${input.id}.${input.hostSuffix}`,
    api_url: `https://api-t-${input.id}.${input.hostSuffix}`,
    db_name: `otterworks_${input.id}`,
    namespace: `otterworks-${input.id}`,
    created_at: now,
    checked_out_at: now,
    expires_at: expiresAt,
    last_seen_at: now,
    persistent: input.persistent === true,
  };

  // Guard against re-checking-out a LIVE tenant. The lock above only serialises
  // concurrent checkout attempts and auto-expires (DynamoDB TTL) ~15min later,
  // so without this a still-active tenant would become checkout-able once the
  // reservation lock lapsed, clobbering its META and double-deploying. Only
  // proceed when there is no existing record, or it is a spent one (free/error).
  try {
    await doc().send(
      new PutCommand({
        TableName: table(),
        Item: item,
        ConditionExpression:
          "attribute_not_exists(PK) OR #s = :free OR #s = :error",
        ExpressionAttributeNames: { "#s": "status" },
        ExpressionAttributeValues: { ":free": "free", ":error": "error" },
      }),
    );
  } catch (err) {
    if (err instanceof Error && err.name === "ConditionalCheckFailedException") {
      // Tenant is live; undo our reservation lock so we don't leave it dangling.
      await releaseLock(input.id).catch(() => {});
      throw new LockConflictError(input.id);
    }
    throw err;
  }
  return itemToTenant(item);
}

/** Delete the reservation lock so the id can be checked out again immediately. */
export async function releaseLock(id: string): Promise<void> {
  await doc().send(
    new DeleteCommand({ TableName: table(), Key: { PK: pkLock(id), SK: SK_LOCK } }),
  );
}

export async function setStatus(id: string, status: TenantStatus): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  await doc().send(
    new UpdateCommand({
      TableName: table(),
      Key: { PK: pkTenant(id), SK: SK_META },
      UpdateExpression: "SET #s = :s, last_seen_at = :now",
      ConditionExpression: "attribute_exists(PK)",
      ExpressionAttributeNames: { "#s": "status" },
      ExpressionAttributeValues: { ":s": status, ":now": now },
    }),
  );
}

export async function checkin(id: string): Promise<void> {
  await setStatus(id, "draining");
}

/**
 * Mark a tenant perpetual, or return it to the TTL regime.
 *
 * The flag and the expiry move together in one update on purpose: the reaper
 * reads the flag and the expiry is the backstop, so a partial write leaves the
 * tenant either immortal with the flag off (never reaped, nobody can tell why)
 * or flagged perpetual over an expiry already in the past.
 */
export async function setPersistent(
  id: string,
  persistent: boolean,
  ttlSeconds: number,
): Promise<number> {
  const now = Math.floor(Date.now() / 1000);
  const expiresAt = now + ttlSeconds;
  await doc().send(
    new UpdateCommand({
      TableName: table(),
      Key: { PK: pkTenant(id), SK: SK_META },
      UpdateExpression: "SET #p = :p, expires_at = :e, last_seen_at = :now",
      ConditionExpression: "attribute_exists(PK)",
      ExpressionAttributeNames: { "#p": "persistent" },
      ExpressionAttributeValues: { ":p": persistent, ":e": expiresAt, ":now": now },
    }),
  );
  return expiresAt;
}

export async function extend(id: string, ttlSeconds: number): Promise<number> {
  const expiresAt = Math.floor(Date.now() / 1000) + ttlSeconds;
  await doc().send(
    new UpdateCommand({
      TableName: table(),
      Key: { PK: pkTenant(id), SK: SK_META },
      UpdateExpression: "SET expires_at = :e, last_seen_at = :now",
      ConditionExpression: "attribute_exists(PK)",
      ExpressionAttributeValues: {
        ":e": expiresAt,
        ":now": Math.floor(Date.now() / 1000),
      },
    }),
  );
  return expiresAt;
}

// Mirrors the defaults in reaper.sh. The reaper treats an absent attribute as
// false, so anything defaulted to true here would show the operator a control
// that is switched on while the reaper is not acting on it.
const DEFAULT_REAPER: ReaperConfig = {
  scheduleCron: "*/15 * * * *",
  graceSeconds: 300,
  enabled: true,
  sweepOrphans: false,
  suspendIdle: false,
  idleAfterSeconds: 3600,
  sweepInfra: false,
  sweepInfraDelete: false,
};

export async function getReaperConfig(): Promise<ReaperConfig> {
  const res = await doc().send(
    new GetCommand({ TableName: table(), Key: { PK: PK_REAPER, SK: SK_CONFIG } }),
  );
  if (!res.Item) return DEFAULT_REAPER;
  const it = res.Item;
  return {
    scheduleCron: String(it.schedule_cron ?? DEFAULT_REAPER.scheduleCron),
    graceSeconds: Number(it.grace_seconds ?? DEFAULT_REAPER.graceSeconds),
    enabled: Boolean(it.enabled ?? DEFAULT_REAPER.enabled),
    sweepOrphans: Boolean(it.sweep_orphans ?? DEFAULT_REAPER.sweepOrphans),
    suspendIdle: Boolean(it.suspend_idle ?? DEFAULT_REAPER.suspendIdle),
    idleAfterSeconds: Number(it.idle_after_seconds ?? DEFAULT_REAPER.idleAfterSeconds),
    sweepInfra: Boolean(it.sweep_infra ?? DEFAULT_REAPER.sweepInfra),
    sweepInfraDelete: Boolean(it.sweep_infra_delete ?? DEFAULT_REAPER.sweepInfraDelete),
    updatedAt: it.updated_at as number | undefined,
    updatedBy: it.updated_by as string | undefined,
  };
}

export async function putReaperConfig(
  cfg: Omit<ReaperConfig, "updatedAt" | "updatedBy">,
  updatedBy: string,
): Promise<ReaperConfig> {
  const now = Math.floor(Date.now() / 1000);
  await doc().send(
    new PutCommand({
      TableName: table(),
      Item: {
        PK: PK_REAPER,
        SK: SK_CONFIG,
        schedule_cron: cfg.scheduleCron,
        grace_seconds: cfg.graceSeconds,
        enabled: cfg.enabled,
        sweep_orphans: cfg.sweepOrphans,
        suspend_idle: cfg.suspendIdle,
        idle_after_seconds: cfg.idleAfterSeconds,
        sweep_infra: cfg.sweepInfra,
        sweep_infra_delete: cfg.sweepInfraDelete,
        updated_at: now,
        updated_by: updatedBy,
      },
    }),
  );
  return { ...cfg, updatedAt: now, updatedBy };
}

export async function appendAudit(evt: Omit<AuditEvent, "ts"> & { ts?: number }): Promise<void> {
  const ts = evt.ts ?? Date.now();
  await doc().send(
    new PutCommand({
      TableName: table(),
      Item: {
        PK: pkAudit(evt.tenantId),
        SK: `${ts}#${evt.action}`,
        actor: evt.actor,
        action: evt.action,
        detail: evt.detail,
        ts,
      },
    }),
  );
}

function itemToAudit(tenantId: string, it: Record<string, unknown>): AuditEvent {
  return {
    tenantId,
    action: it.action as AuditAction,
    actor: String(it.actor ?? ""),
    detail: it.detail as string | undefined,
    ts: Number(it.ts ?? 0),
  };
}

export async function queryAudit(tenantId: string, limit = 100): Promise<AuditEvent[]> {
  const res = await doc().send(
    new QueryCommand({
      TableName: table(),
      KeyConditionExpression: "PK = :pk",
      ExpressionAttributeValues: { ":pk": pkAudit(tenantId) },
      ScanIndexForward: false,
      Limit: limit,
    }),
  );
  return (res.Items ?? []).map((it) => itemToAudit(tenantId, it));
}

// Recent audit across all tenants (scan; small N per the schema notes).
export async function scanAudit(limit = 100): Promise<AuditEvent[]> {
  const items: { tenantId: string; raw: Record<string, unknown> }[] = [];
  let lastKey: Record<string, unknown> | undefined;
  do {
    const res = await doc().send(
      new ScanCommand({
        TableName: table(),
        FilterExpression: "begins_with(PK, :p)",
        ExpressionAttributeValues: { ":p": "AUDIT#" },
        ExclusiveStartKey: lastKey,
      }),
    );
    for (const it of res.Items ?? []) {
      const pk = String((it as Record<string, unknown>).PK ?? "");
      items.push({ tenantId: pk.replace(/^AUDIT#/, ""), raw: it as Record<string, unknown> });
    }
    lastKey = res.LastEvaluatedKey as Record<string, unknown> | undefined;
  } while (lastKey);

  return items
    .map(({ tenantId, raw }) => itemToAudit(tenantId, raw))
    .sort((a, b) => b.ts - a.ts)
    .slice(0, limit);
}
