import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, Subject, map } from 'rxjs';
import { User, UserActivity } from '../models/user.model';
import { AuditEvent } from '../models/audit.model';
import { FeatureFlag } from '../models/feature-flag.model';
import { Announcement } from '../models/announcement.model';
import { ServiceHealth } from '../models/system-health.model';
import { DashboardStats, AnalyticsReport, ChartDataPoint } from '../models/analytics.model';
import { Incident } from '../models/incident.model';

// Service metadata not available from the health endpoint — kept here for display purposes
const SERVICE_META: Record<string, { version: string; port: number; language: string; details: string }> = {
  'auth-service':         { version: '3.1.0', port: 8081, language: 'Java 17',     details: 'Authentication, authorization, user management' },
  'file-service':         { version: '1.8.2', port: 8082, language: 'Rust 1.77',   details: 'File upload/download, S3 integration, versioning' },
  'document-service':     { version: '2.2.0', port: 8083, language: 'Python 3.12', details: 'Document editing, versioning, templates' },
  'collab-service':       { version: '1.5.3', port: 8084, language: 'Node.js 20',  details: 'Real-time collaborative editing (CRDT)' },
  'notification-service': { version: '1.3.1', port: 8086, language: 'Kotlin 1.9',  details: 'Event-driven notifications (email, in-app, webhook)' },
  'search-service':       { version: '1.1.0', port: 8087, language: 'Python 3.12', details: 'Full-text search powered by MeiliSearch' },
  'analytics-service':    { version: '1.2.0', port: 8088, language: 'Scala 3.4',   details: 'Usage analytics, data aggregation' },
  'audit-service':        { version: '2.0.1', port: 8090, language: 'C# 12',       details: 'Immutable audit trail, compliance' },
};

// Backend uses severity (info|warning|critical|maintenance), frontend uses priority (low|medium|high|critical)
const SEVERITY_TO_PRIORITY: Record<string, Announcement['priority']> = {
  info: 'low', warning: 'medium', critical: 'critical', maintenance: 'high',
};
const PRIORITY_TO_SEVERITY: Record<string, string> = {
  low: 'info', medium: 'warning', critical: 'critical', high: 'maintenance',
};

@Injectable({ providedIn: 'root' })
export class AdminApiService {
  private readonly baseUrl = '/api/v1';

  readonly statsChanged$ = new Subject<void>();

  constructor(private http: HttpClient) {}

  // ── Dashboard ────────────────────────────────────────────────────────────

  getDashboardStats(): Observable<DashboardStats> {
    return this.http.get<any>(`${this.baseUrl}/admin/metrics/summary`).pipe(
      map(res => this.mapDashboardStats(res)),
    );
  }

  // ── Users ────────────────────────────────────────────────────────────────

  getUsers(): Observable<User[]> {
    return this.http.get<any>(`${this.baseUrl}/admin/users`).pipe(
      map(res => (res.users || []).map((u: any) => this.mapUser(u))),
    );
  }

  getUser(id: string): Observable<User | undefined> {
    return this.http.get<any>(`${this.baseUrl}/admin/users/${id}`).pipe(
      map(raw => this.mapUser(raw)),
    );
  }

  getUserActivity(userId: string): Observable<UserActivity[]> {
    return this.http.get<any>(`${this.baseUrl}/admin/audit-logs?actor_id=${userId}&per_page=20`).pipe(
      map(res => (res.audit_logs || []).map((e: any) => this.mapUserActivity(e))),
    );
  }

  suspendUser(userId: string): Observable<User> {
    return this.http.put<any>(`${this.baseUrl}/admin/users/${userId}/suspend`, {}).pipe(
      map(raw => this.mapUser(raw)),
    );
  }

  restoreUser(userId: string): Observable<User> {
    return this.http.put<any>(`${this.baseUrl}/admin/users/${userId}/activate`, {}).pipe(
      map(raw => this.mapUser(raw)),
    );
  }

  deleteUser(userId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/admin/users/${userId}`);
  }

  deleteDocument(docId: string, _fileSizeBytes = 275 * 1024 * 1024): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/admin/documents/${docId}`);
  }

  // ── Audit Logs ───────────────────────────────────────────────────────────

  getAuditEvents(): Observable<AuditEvent[]> {
    return this.http.get<any>(`${this.baseUrl}/admin/audit-logs`).pipe(
      map(res => (res.audit_logs || []).map((e: any) => this.mapAuditEvent(e))),
    );
  }

  // ── Feature Flags ────────────────────────────────────────────────────────

  getFeatureFlags(): Observable<FeatureFlag[]> {
    return this.http.get<any>(`${this.baseUrl}/admin/features`).pipe(
      map(res => (res.features || []).map((f: any) => this.mapFeatureFlag(f))),
    );
  }

  toggleFeatureFlag(flagId: string, enabled: boolean): Observable<FeatureFlag> {
    return this.http.put<any>(`${this.baseUrl}/admin/features/${flagId}`, { feature: { enabled } }).pipe(
      map(raw => this.mapFeatureFlag(raw)),
    );
  }

  // ── System Health ────────────────────────────────────────────────────────

  getSystemHealth(): Observable<ServiceHealth[]> {
    return this.http.get<any>(`${this.baseUrl}/admin/health/services`).pipe(
      map(res => this.mapHealthServices(res)),
    );
  }

  // ── Announcements ────────────────────────────────────────────────────────

  getAnnouncements(): Observable<Announcement[]> {
    return this.http.get<any>(`${this.baseUrl}/admin/announcements`).pipe(
      map(res => (res.announcements || []).map((a: any) => this.mapAnnouncement(a))),
    );
  }

  createAnnouncement(announcement: Partial<Announcement>): Observable<Announcement> {
    const payload = {
      announcement: {
        title: announcement.title,
        body: announcement.content,
        severity: PRIORITY_TO_SEVERITY[announcement.priority ?? 'medium'] ?? 'info',
        status: 'draft',
        target_audience: announcement.targetAudience ? { role: announcement.targetAudience } : {},
      },
    };
    return this.http.post<any>(`${this.baseUrl}/admin/announcements`, payload).pipe(
      map(raw => this.mapAnnouncement(raw)),
    );
  }

  publishAnnouncement(id: string): Observable<Announcement> {
    return this.http.put<any>(`${this.baseUrl}/admin/announcements/${id}`, { announcement: { status: 'published' } }).pipe(
      map(raw => this.mapAnnouncement(raw)),
    );
  }

  deleteAnnouncement(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/admin/announcements/${id}`);
  }

  // ── Storage Quotas ───────────────────────────────────────────────────────

  updateStorageQuota(userId: string, quota: number): Observable<User> {
    return this.http.put<any>(`${this.baseUrl}/admin/quotas/${userId}`, { quota: { quota_bytes: quota } }).pipe(
      map(raw => ({
        id: userId,
        email: '',
        displayName: '',
        role: 'viewer' as const,
        status: 'active' as const,
        storageUsed: raw.used_bytes ?? 0,
        storageQuota: raw.quota_bytes ?? quota,
        lastLogin: '',
        createdAt: '',
        documentsCount: 0,
      })),
    );
  }

  // ── Analytics ────────────────────────────────────────────────────────────

  getAnalyticsReport(): Observable<AnalyticsReport> {
    return this.http.get<any>(`${this.baseUrl}/admin/metrics/summary`).pipe(
      map(res => this.mapAnalyticsReport(res)),
    );
  }

  // ── Incidents ────────────────────────────────────────────────────────────

  getIncidents(): Observable<Incident[]> {
    return this.http.get<any>(`${this.baseUrl}/admin/incidents`).pipe(
      map(res => (res.incidents || []).map((i: any) => this.mapIncident(i))),
    );
  }

  getIncident(id: string): Observable<Incident> {
    return this.http.get<any>(`${this.baseUrl}/admin/incidents/${id}`).pipe(
      map(res => this.mapIncident(res.incident || res)),
    );
  }

  createIncident(incident: Partial<Incident>): Observable<Incident> {
    const payload = {
      incident: {
        title: incident.title,
        description: incident.description,
        severity: incident.severity,
        affected_service: incident.affectedService,
      },
    };
    return this.http.post<any>(`${this.baseUrl}/admin/incidents`, payload).pipe(
      map(res => this.mapIncident(res.incident || res)),
    );
  }

  triggerDevinSession(incidentId: string): Observable<Incident> {
    return this.http.post<any>(`${this.baseUrl}/admin/incidents/${incidentId}/trigger_session`, {}).pipe(
      map(res => this.mapIncident(res.incident || res)),
    );
  }

  updateIncidentStatus(id: string, status: string): Observable<Incident> {
    return this.http.patch<any>(`${this.baseUrl}/admin/incidents/${id}`, { incident: { status } }).pipe(
      map(res => this.mapIncident(res.incident || res)),
    );
  }

  deleteIncident(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/admin/incidents/${id}`);
  }

  // ── Chaos injection (demo/workshop controls) ─────────────────────────────

  triggerChaos(service: string, scenario: string): Observable<{ status: string; key: string; expires_in: number }> {
    return this.http.post<any>(`${this.baseUrl}/admin/chaos`, { service, scenario });
  }

  resetChaos(): Observable<{ status: string; cleared: string[] }> {
    return this.http.delete<any>(`${this.baseUrl}/admin/chaos`);
  }

  // ── Settings ──────────────────────────────────────────────────────────────

  getAutoInvestigate(): Observable<{ enabled: boolean }> {
    return this.http.get<{ enabled: boolean }>(`${this.baseUrl}/admin/settings/auto_investigate`);
  }

  setAutoInvestigate(enabled: boolean): Observable<{ enabled: boolean }> {
    return this.http.put<{ enabled: boolean }>(`${this.baseUrl}/admin/settings/auto_investigate`, { enabled });
  }

  // ── Private mappers ──────────────────────────────────────────────────────

  private mapUser(raw: any): User {
    const quota = raw.storage_quota;
    return {
      id: raw.id,
      email: raw.email,
      displayName: raw.display_name,
      role: raw.role,
      status: raw.status,
      avatarUrl: raw.avatar_url,
      storageUsed: quota?.used_bytes ?? 0,
      storageQuota: quota?.quota_bytes ?? 5 * 1024 * 1024 * 1024,
      lastLogin: raw.last_login_at ?? raw.created_at,
      createdAt: raw.created_at,
      department: raw.metadata?.department ?? '',
      documentsCount: raw.metadata?.documents_count ?? 0,
    };
  }

  private mapUserActivity(raw: any): UserActivity {
    return {
      id: raw.id,
      userId: raw.actor_id,
      action: raw.action,
      resource: `${raw.resource_type}${raw.resource_id ? ' ' + raw.resource_id : ''}`,
      timestamp: raw.created_at,
      details: raw.changes_made ? JSON.stringify(raw.changes_made) : undefined,
      ipAddress: raw.ip_address,
    };
  }

  private mapAuditEvent(raw: any): AuditEvent {
    return {
      id: raw.id,
      timestamp: raw.created_at,
      userId: raw.actor_id ?? '',
      userName: raw.actor_email ?? raw.actor_id ?? 'System',
      action: this.normalizeAuditAction(raw.action),
      resourceType: raw.resource_type ?? '',
      resourceName: raw.resource_id ?? '',
      details: raw.changes_made ? JSON.stringify(raw.changes_made) : raw.action,
      ipAddress: raw.ip_address ?? '',
      severity: this.auditSeverity(raw.action),
    };
  }

  private normalizeAuditAction(action: string): AuditEvent['action'] {
    if (!action) return 'update';
    if (action.includes('created'))                          return 'create';
    if (action.includes('deleted'))                          return 'delete';
    if (action.includes('suspended'))                        return 'suspend';
    if (action.includes('activated') || action.includes('restored')) return 'restore';
    if (action.includes('login'))                            return 'login';
    if (action.includes('logout'))                           return 'logout';
    if (action.includes('upload'))                           return 'upload';
    if (action.includes('download'))                         return 'download';
    if (action.includes('share'))                            return 'share';
    return 'update';
  }

  private auditSeverity(action: string): AuditEvent['severity'] {
    if (!action) return 'info';
    if (action.includes('deleted') || action.includes('suspended') || action.includes('bulk')) return 'warning';
    if (action.includes('config')) return 'warning';
    return 'info';
  }

  private mapFeatureFlag(raw: any): FeatureFlag {
    return {
      id: raw.id,
      name: raw.name,
      key: raw.name?.toLowerCase().replace(/\s+/g, '-') ?? raw.id,
      description: raw.description ?? '',
      enabled: raw.enabled,
      category: raw.metadata?.category ?? 'General',
      updatedAt: raw.updated_at,
      updatedBy: raw.metadata?.updated_by ?? 'Admin',
    };
  }

  private mapAnnouncement(raw: any): Announcement {
    return {
      id: raw.id,
      title: raw.title,
      content: raw.body,
      priority: SEVERITY_TO_PRIORITY[raw.severity] ?? 'medium',
      status: raw.status,
      createdAt: raw.created_at,
      publishedAt: raw.status === 'published' ? (raw.starts_at ?? raw.updated_at) : undefined,
      expiresAt: raw.ends_at,
      createdBy: raw.created_by ?? 'Admin',
      targetAudience: raw.target_audience?.role ?? 'all',
    };
  }

  private mapHealthStatus(status: string): ServiceHealth['status'] {
    if (status === 'healthy') return 'healthy';
    if (status === 'unhealthy') return 'down';
    return 'degraded';
  }

  private mapHealthServices(res: any): ServiceHealth[] {
    const services: ServiceHealth[] = (res.services || []).map((s: any) => {
      const meta = SERVICE_META[s.name] ?? { version: 'unknown', port: 0, language: 'unknown', details: s.message ?? '' };
      return {
        name: s.name,
        status: this.mapHealthStatus(s.status),
        uptime: 'N/A',
        responseTime: s.latency_ms ?? 0,
        lastChecked: res.timestamp ?? new Date().toISOString(),
        version: meta.version,
        port: meta.port,
        language: meta.language,
        details: s.message ?? meta.details,
      } as ServiceHealth;
    });

    // Append database and redis as pseudo-services if present in the response
    if (res.database) {
      services.push({
        name: 'postgres',
        status: res.database.status === 'healthy' ? 'healthy' : 'down',
        uptime: 'N/A',
        responseTime: res.database.latency_ms ?? 0,
        lastChecked: res.timestamp ?? new Date().toISOString(),
        version: '15',
        port: 5432,
        language: 'PostgreSQL',
        details: res.database.message ?? 'Primary database',
      });
    }
    if (res.redis) {
      services.push({
        name: 'redis',
        status: res.redis.status === 'healthy' ? 'healthy' : 'down',
        uptime: 'N/A',
        responseTime: res.redis.latency_ms ?? 0,
        lastChecked: res.timestamp ?? new Date().toISOString(),
        version: '7',
        port: 6379,
        language: 'Redis',
        details: res.redis.message ?? 'In-memory cache',
      });
    }

    return services;
  }

  private mapDashboardStats(res: any): DashboardStats {
    const users = res.users ?? {};
    const storage = res.storage ?? {};
    const usedBytes: number = storage.total_used_bytes ?? 0;
    return {
      totalUsers: users.total ?? 0,
      activeDocuments: 0,  // not tracked by admin-service metrics
      storageUsed: this.formatBytes(usedBytes),
      activeSessions: users.active ?? 0,
      usersGrowth: 0,
      documentsGrowth: 0,
      storageGrowth: 0,
      sessionsGrowth: 0,
    };
  }

  private mapAnalyticsReport(res: any): AnalyticsReport {
    const users = res.users ?? {};
    const storage = res.storage ?? {};
    const audit = res.audit ?? {};

    const byRole: Record<string, number> = users.by_role ?? {};
    const byTier: Record<string, number> = storage.by_tier ?? {};
    const topActions: Record<string, number> = audit.top_actions ?? {};

    const rolePoints: ChartDataPoint[] = Object.entries(byRole).map(([label, value]) => ({ label, value: value as number }));
    const tierPoints: ChartDataPoint[] = Object.entries(byTier).map(([label, value]) => ({ label, value: value as number }));
    const actionPoints: ChartDataPoint[] = Object.entries(topActions).map(([label, value]) => ({ label, value: value as number }));

    return {
      userSignups: rolePoints.length ? rolePoints : [{ label: 'Users', value: users.total ?? 0 }],
      storageUsage: tierPoints.length ? tierPoints : [{ label: 'Used', value: storage.total_used_bytes ?? 0 }],
      documentActivity: actionPoints.length ? actionPoints : [{ label: 'Events', value: audit.total_events ?? 0 }],
      activeUsers: [
        { label: 'Active',    value: users.active    ?? 0 },
        { label: 'Suspended', value: users.suspended ?? 0 },
      ],
      topFileTypes: [],  // file-type breakdown not available from admin-service metrics
      peakHours: [],
    };
  }

  private formatBytes(bytes: number): string {
    if (bytes >= 1024 ** 4) return `${(bytes / 1024 ** 4).toFixed(1)} TB`;
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  }

  private mapIncident(raw: any): Incident {
    return {
      id: raw.id,
      title: raw.title,
      description: raw.description,
      severity: raw.severity,
      status: raw.status,
      affectedService: raw.affected_service,
      devinSessionId: raw.devin_session_id,
      devinSessionUrl: raw.devin_session_url,
      devinSessionStatus: raw.devin_session_status,
      reporterId: raw.reporter_id,
      resolvedAt: raw.resolved_at,
      closedAt: raw.closed_at,
      active: raw.active,
      createdAt: raw.created_at,
      updatedAt: raw.updated_at,
    };
  }
}
