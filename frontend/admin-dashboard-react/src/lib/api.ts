import { apiClient } from "./api-client";

// ── Types (post camelCase interceptor shapes) ─────────────────────────────

export type IncidentSeverity = "low" | "medium" | "high" | "critical";
export type IncidentStatus = "open" | "investigating" | "resolved" | "closed";

export interface Incident {
  id: string;
  title: string;
  description: string;
  severity: IncidentSeverity;
  status: IncidentStatus;
  affectedService: string | null;
  devinSessionId: string | null;
  devinSessionUrl: string | null;
  devinSessionStatus: string | null;
  reporterId: string | null;
  resolvedAt: string | null;
  closedAt: string | null;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface NewIncident {
  title: string;
  description: string;
  severity: IncidentSeverity;
  affectedService: string;
}

export const AFFECTED_SERVICES = [
  { value: "api-gateway", label: "API Gateway (Go)" },
  { value: "auth-service", label: "Auth Service (Java)" },
  { value: "file-service", label: "File Service (Rust)" },
  { value: "document-service", label: "Document Service (Python)" },
  { value: "collab-service", label: "Collaboration Service (Node.js)" },
  { value: "notification-service", label: "Notification Service (Kotlin)" },
  { value: "search-service", label: "Search Service (Python)" },
  { value: "analytics-service", label: "Analytics Service (Scala)" },
  { value: "admin-service", label: "Admin Service (Ruby)" },
  { value: "audit-service", label: "Audit Service (C#)" },
  { value: "report-service", label: "Report Service (Java)" },
];

export interface QuotaUser {
  id: string;
  email: string;
  displayName: string;
  role: string;
  status: string;
  storageUsed: number;
  storageQuota: number;
  lastLogin: string;
  createdAt: string;
  department: string;
  documentsCount: number;
}

interface RawIncident {
  id: string;
  title: string;
  description: string;
  severity: IncidentSeverity;
  status: IncidentStatus;
  affectedService?: string | null;
  devinSessionId?: string | null;
  devinSessionUrl?: string | null;
  devinSessionStatus?: string | null;
  reporterId?: string | null;
  resolvedAt?: string | null;
  closedAt?: string | null;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

interface RawUser {
  id: string;
  email: string;
  displayName: string;
  role?: string;
  status?: string;
  storageQuota?: { usedBytes?: number; quotaBytes?: number } | null;
  lastLoginAt?: string | null;
  createdAt?: string;
  metadata?: { department?: string; documentsCount?: number } | null;
}

function mapIncident(raw: RawIncident): Incident {
  return {
    id: raw.id,
    title: raw.title,
    description: raw.description,
    severity: raw.severity,
    status: raw.status,
    affectedService: raw.affectedService ?? null,
    devinSessionId: raw.devinSessionId ?? null,
    devinSessionUrl: raw.devinSessionUrl ?? null,
    devinSessionStatus: raw.devinSessionStatus ?? null,
    reporterId: raw.reporterId ?? null,
    resolvedAt: raw.resolvedAt ?? null,
    closedAt: raw.closedAt ?? null,
    active: raw.active,
    createdAt: raw.createdAt,
    updatedAt: raw.updatedAt,
  };
}

function mapQuotaUser(raw: RawUser): QuotaUser {
  return {
    id: raw.id,
    email: raw.email,
    displayName: raw.displayName,
    role: raw.role ?? "",
    status: raw.status ?? "",
    storageUsed: raw.storageQuota?.usedBytes ?? 0,
    storageQuota: raw.storageQuota?.quotaBytes ?? 5 * 1024 * 1024 * 1024,
    lastLogin: raw.lastLoginAt ?? raw.createdAt ?? "",
    createdAt: raw.createdAt ?? "",
    department: raw.metadata?.department ?? "",
    documentsCount: raw.metadata?.documentsCount ?? 0,
  };
}

// ── Incidents ──────────────────────────────────────────────────────────────

export async function getIncidents(): Promise<Incident[]> {
  const res = await apiClient.get<{ incidents?: RawIncident[] }>("/admin/incidents");
  return (res.data.incidents ?? []).map(mapIncident);
}

export async function createIncident(incident: NewIncident): Promise<Incident> {
  const payload = {
    incident: {
      title: incident.title,
      description: incident.description,
      severity: incident.severity,
      affected_service: incident.affectedService,
    },
  };
  const res = await apiClient.post<{ incident?: RawIncident } & RawIncident>(
    "/admin/incidents",
    payload
  );
  return mapIncident(res.data.incident ?? res.data);
}

export async function updateIncidentStatus(
  id: string,
  status: IncidentStatus
): Promise<Incident> {
  const res = await apiClient.patch<{ incident?: RawIncident } & RawIncident>(
    `/admin/incidents/${id}`,
    { incident: { status } }
  );
  return mapIncident(res.data.incident ?? res.data);
}

export async function deleteIncident(id: string): Promise<void> {
  await apiClient.delete(`/admin/incidents/${id}`);
}

export async function triggerDevinSession(incidentId: string): Promise<Incident> {
  const res = await apiClient.post<{ incident?: RawIncident } & RawIncident>(
    `/admin/incidents/${incidentId}/trigger_session`,
    {}
  );
  return mapIncident(res.data.incident ?? res.data);
}

// ── Settings ───────────────────────────────────────────────────────────────

export async function getAutoInvestigate(): Promise<{ enabled: boolean }> {
  const res = await apiClient.get<{ enabled: boolean }>(
    "/admin/settings/auto_investigate"
  );
  return res.data;
}

export async function setAutoInvestigate(
  enabled: boolean
): Promise<{ enabled: boolean }> {
  const res = await apiClient.put<{ enabled: boolean }>(
    "/admin/settings/auto_investigate",
    { enabled }
  );
  return res.data;
}

// ── Chaos injection (demo/workshop controls) ───────────────────────────────

export interface ChaosResetResult {
  status: string;
  cleared: string[];
  resolvedIncidents?: string[];
}

export async function triggerChaos(
  service: string,
  scenario: string
): Promise<{ status: string; key: string; expiresIn: number }> {
  const res = await apiClient.post("/admin/chaos", { service, scenario });
  return res.data;
}

export async function resetChaos(): Promise<ChaosResetResult> {
  const res = await apiClient.delete<ChaosResetResult>("/admin/chaos");
  return res.data;
}

// ── Users / Storage quotas ─────────────────────────────────────────────────

export async function getUsers(): Promise<QuotaUser[]> {
  const res = await apiClient.get<{ users?: RawUser[] }>("/admin/users");
  return (res.data.users ?? []).map(mapQuotaUser);
}

export async function updateStorageQuota(
  userId: string,
  quotaBytes: number
): Promise<{ usedBytes: number; quotaBytes: number }> {
  const res = await apiClient.put<{ usedBytes?: number; quotaBytes?: number }>(
    `/admin/quotas/${userId}`,
    { quota: { quota_bytes: quotaBytes } }
  );
  return {
    usedBytes: res.data.usedBytes ?? 0,
    quotaBytes: res.data.quotaBytes ?? quotaBytes,
  };
}

export function apiErrorDetail(err: unknown, fallback: string): string {
  const data = (err as { response?: { data?: { details?: string; error?: string } } })
    ?.response?.data;
  return data?.details || data?.error || fallback;
}
