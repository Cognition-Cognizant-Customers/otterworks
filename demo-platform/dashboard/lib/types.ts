// Shared types for the Demo Ops Dashboard. `Tenant` mirrors the API contract
// (demo-platform/docs/api-contract.md) exactly.

export type TenantStatus =
  | "free"
  | "reserved"
  | "deploying"
  | "active"
  | "draining"
  | "error";

export type TenantTier = "A" | "B";

export interface ServiceLiveState {
  name: string;
  ready: boolean;
  restarts: number;
}

export interface TenantLiveState {
  phase: string;
  readyPods: number;
  totalPods: number;
  services: ServiceLiveState[];
}

export interface Tenant {
  id: string;
  status: TenantStatus;
  owner?: string;
  branch?: string;
  tier: TenantTier;
  imageTag?: string;
  url?: string;
  apiUrl?: string;
  dbName: string;
  namespace: string;
  createdAt: number;
  expiresAt: number;
  lastSeenAt: number;
  note?: string;
  // Perpetual tenant: exempt from the reaper and from idle-suspend, and not
  // checkin-able or bug-injectable without explicitly clearing the flag first.
  persistent?: boolean;
  live?: TenantLiveState;
}

export interface PodInfo {
  name: string;
  phase: string;
  ready: boolean;
  restarts: number;
  containers: { name: string; ready: boolean; restarts: number }[];
}

// Audit `action` set from control-table-schema.md.
export type AuditAction =
  | "checkout"
  | "checkin"
  | "extend"
  | "deploy_ok"
  | "deploy_fail"
  | "reap"
  | "inject"
  | "reset"
  | "redeploy"
  | "persist"
  | "login_ok"
  | "login_fail";

export interface AuditEvent {
  tenantId: string;
  action: AuditAction;
  actor: string;
  detail?: string;
  ts: number;
}

export interface ReaperConfig {
  scheduleCron: string;
  graceSeconds: number;
  enabled: boolean;
  sweepOrphans: boolean;
  suspendIdle: boolean;
  idleAfterSeconds: number;
  sweepInfra: boolean;
  sweepInfraDelete: boolean;
  updatedAt?: number;
  updatedBy?: string;
}

export interface Orphan {
  kind: "namespace";
  name: string;
  detail?: string;
}

export interface TenantDetail extends Tenant {
  pods: PodInfo[];
  audit: AuditEvent[];
  logs?: string;
}

// Request payload shapes for the mutating routes.
export interface CheckoutRequest {
  id?: string;
  branch: string;
  owner: string;
  tier?: TenantTier;
  ttl?: string;
  image_tag?: string;
  persistent?: boolean;
}

export interface RedeployRequest {
  image_tag?: string;
  branch?: string;
}

export interface PersistRequest {
  persistent: boolean;
}

export interface ExtendRequest {
  ttl: string;
}

export interface InjectRequest {
  scenario: string;
}

export interface ReaperUpdateRequest {
  schedule_cron: string;
  grace_seconds: number;
  enabled: boolean;
  sweep_orphans: boolean;
  suspend_idle: boolean;
  idle_after_seconds: number;
  sweep_infra: boolean;
  sweep_infra_delete: boolean;
}
