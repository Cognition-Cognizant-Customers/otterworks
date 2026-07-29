import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AdminApiService } from './admin-api.service';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

const rawUser = {
  id: 'user-001',
  email: 'alice@example.com',
  display_name: 'Alice',
  role: 'admin',
  status: 'active',
  storage_quota: { used_bytes: 1024, quota_bytes: 5368709120 },
  created_at: '2026-01-01T00:00:00Z',
  metadata: { department: 'Engineering', documents_count: 3 },
};

const rawAuditLog = {
  id: 'audit-001',
  actor_id: 'user-001',
  actor_email: 'alice@example.com',
  action: 'user.created',
  resource_type: 'user',
  resource_id: 'user-002',
  ip_address: '10.0.0.1',
  created_at: '2026-01-01T00:00:00Z',
};

const metricsSummary = {
  users: { total: 5, active: 3, suspended: 1, by_role: { admin: 2, editor: 3 } },
  storage: { total_used_bytes: 1073741824, by_tier: { standard: 5 } },
  audit: { total_events: 10, top_actions: { login: 5 } },
};

describe('AdminApiService', () => {
  let service: AdminApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
    imports: [],
    providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()]
});
    service = TestBed.inject(AdminApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should return dashboard stats', () => {
    service.getDashboardStats().subscribe(stats => {
      expect(stats).toBeTruthy();
      expect(stats.totalUsers).toBe(5);
      expect(stats.activeDocuments).toBe(0); // not tracked by admin-service metrics
      expect(stats.storageUsed).toBe('1.0 GB');
      expect(stats.activeSessions).toBe(3);
    });
    httpMock.expectOne('/api/v1/admin/metrics/summary').flush(metricsSummary);
  });

  it('should return users list', () => {
    service.getUsers().subscribe(users => {
      expect(users.length).toBe(1);
      expect(users[0].displayName).toBe('Alice');
      expect(users[0].email).toBe('alice@example.com');
      expect(users[0].storageUsed).toBe(1024);
    });
    httpMock.expectOne('/api/v1/admin/users').flush({ users: [rawUser] });
  });

  it('should return a single user by id', () => {
    service.getUser('user-001').subscribe(user => {
      expect(user).toBeTruthy();
      expect(user!.id).toBe('user-001');
    });
    httpMock.expectOne('/api/v1/admin/users/user-001').flush(rawUser);
  });

  it('should return audit events', () => {
    service.getAuditEvents().subscribe(events => {
      expect(events.length).toBe(1);
      expect(events[0].action).toBe('create');
      expect(events[0].userName).toBe('alice@example.com');
    });
    httpMock.expectOne('/api/v1/admin/audit-logs').flush({ audit_logs: [rawAuditLog] });
  });

  it('should return feature flags', () => {
    service.getFeatureFlags().subscribe(flags => {
      expect(flags.length).toBe(1);
      expect(flags[0].name).toBe('Dark Mode');
      expect(flags[0].key).toBe('dark-mode');
    });
    httpMock.expectOne('/api/v1/admin/features').flush({
      features: [{ id: 'flag-001', name: 'Dark Mode', enabled: true, updated_at: '2026-01-01T00:00:00Z' }],
    });
  });

  it('should return system health', () => {
    service.getSystemHealth().subscribe(services => {
      expect(services.length).toBe(4); // 2 services + postgres + redis
      expect(services[0].name).toBe('auth-service');
      expect(services[0].status).toBe('healthy');
      expect(services[1].status).toBe('down');
    });
    httpMock.expectOne('/api/v1/admin/health/services').flush({
      services: [
        { name: 'auth-service', status: 'healthy', latency_ms: 12 },
        { name: 'file-service', status: 'unhealthy', latency_ms: 0, message: 'connection refused' },
      ],
      database: { status: 'healthy', latency_ms: 2 },
      redis: { status: 'healthy', latency_ms: 1 },
      timestamp: '2026-01-01T00:00:00Z',
    });
  });

  it('should return announcements', () => {
    service.getAnnouncements().subscribe(announcements => {
      expect(announcements.length).toBe(1);
      expect(announcements[0].content).toBe('Scheduled maintenance tonight');
      expect(announcements[0].priority).toBe('medium');
    });
    httpMock.expectOne('/api/v1/admin/announcements').flush({
      announcements: [{
        id: 'ann-001', title: 'Maintenance', body: 'Scheduled maintenance tonight',
        severity: 'warning', status: 'published', created_at: '2026-01-01T00:00:00Z',
      }],
    });
  });

  it('should return analytics report', () => {
    service.getAnalyticsReport().subscribe(report => {
      expect(report.userSignups.length).toBe(2);
      expect(report.activeUsers).toEqual([
        { label: 'Active', value: 3 },
        { label: 'Suspended', value: 1 },
      ]);
      expect(report.storageUsage.length).toBe(1);
    });
    httpMock.expectOne('/api/v1/admin/metrics/summary').flush(metricsSummary);
  });

  it('should toggle feature flag', () => {
    service.toggleFeatureFlag('flag-001', true).subscribe(flag => {
      expect(flag.enabled).toBeTrue();
    });
    const req = httpMock.expectOne('/api/v1/admin/features/flag-001');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ feature: { enabled: true } });
    req.flush({ id: 'flag-001', name: 'Dark Mode', enabled: true });
  });

  it('should suspend a user', () => {
    service.suspendUser('user-001').subscribe(user => {
      expect(user.status).toBe('suspended');
    });
    const req = httpMock.expectOne('/api/v1/admin/users/user-001/suspend');
    expect(req.request.method).toBe('PUT');
    req.flush({ ...rawUser, status: 'suspended' });
  });

  describe('Incident API methods', () => {
    it('updateIncidentStatus should make a PATCH request', () => {
      const mockResponse = {
        id: 'inc-1', title: 'Test', description: 'Desc', severity: 'high',
        status: 'resolved', affected_service: null, devin_session_id: null,
        devin_session_url: null, devin_session_status: null, reporter_id: null,
        resolved_at: '2026-01-01T00:00:00Z', closed_at: null, active: false,
        created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
      };

      service.updateIncidentStatus('inc-1', 'resolved').subscribe(incident => {
        expect(incident).toBeTruthy();
        expect(incident.status).toBe('resolved');
        expect(incident.id).toBe('inc-1');
      });

      const req = httpMock.expectOne('/api/v1/admin/incidents/inc-1');
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual({ incident: { status: 'resolved' } });
      req.flush(mockResponse);
    });

    it('deleteIncident should make a DELETE request', () => {
      service.deleteIncident('inc-1').subscribe();

      const req = httpMock.expectOne('/api/v1/admin/incidents/inc-1');
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
    });
  });
});
