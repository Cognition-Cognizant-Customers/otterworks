import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AdminApiService } from './admin-api.service';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

const METRICS_RESPONSE = {
  users: { total: 42, active: 30, suspended: 2, by_role: { admin: 2, editor: 20, viewer: 20 } },
  storage: { total_used_bytes: 5 * 1024 ** 3, by_tier: { free: 30, pro: 12 } },
  audit: { total_events: 100, top_actions: { 'user.login': 60, 'file.upload': 40 } },
};

const USER_RESPONSE = {
  id: 'user-001',
  email: 'alice@otterworks.io',
  display_name: 'Alice',
  role: 'admin',
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: '2026-01-02T00:00:00Z',
  storage_quota: { used_bytes: 1024, quota_bytes: 2048 },
  metadata: { department: 'Engineering', documents_count: 5 },
};

describe('AdminApiService', () => {
  let service: AdminApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()],
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
    let stats: any;
    service.getDashboardStats().subscribe(s => (stats = s));

    const req = httpMock.expectOne('/api/v1/admin/metrics/summary');
    expect(req.request.method).toBe('GET');
    req.flush(METRICS_RESPONSE);

    expect(stats).toBeTruthy();
    expect(stats.totalUsers).toBe(42);
    expect(stats.activeSessions).toBe(30);
    expect(stats.storageUsed).toBe('5.0 GB');
  });

  it('should return users list', () => {
    let users: any[] = [];
    service.getUsers().subscribe(u => (users = u));

    const req = httpMock.expectOne('/api/v1/admin/users');
    expect(req.request.method).toBe('GET');
    req.flush({ users: [USER_RESPONSE] });

    expect(users.length).toBe(1);
    expect(users[0].displayName).toBe('Alice');
    expect(users[0].email).toBe('alice@otterworks.io');
    expect(users[0].department).toBe('Engineering');
  });

  it('should return a single user by id', () => {
    let user: any;
    service.getUser('user-001').subscribe(u => (user = u));

    const req = httpMock.expectOne('/api/v1/admin/users/user-001');
    expect(req.request.method).toBe('GET');
    req.flush(USER_RESPONSE);

    expect(user).toBeTruthy();
    expect(user.id).toBe('user-001');
    expect(user.storageUsed).toBe(1024);
    expect(user.storageQuota).toBe(2048);
  });

  it('should return audit events', () => {
    let events: any[] = [];
    service.getAuditEvents().subscribe(e => (events = e));

    const req = httpMock.expectOne('/api/v1/admin/audit-logs');
    expect(req.request.method).toBe('GET');
    req.flush({
      audit_logs: [{
        id: 'evt-1', created_at: '2026-01-01T00:00:00Z', actor_id: 'user-001',
        actor_email: 'alice@otterworks.io', action: 'user.suspended',
        resource_type: 'user', resource_id: 'user-002', ip_address: '10.0.0.1',
      }],
    });

    expect(events.length).toBe(1);
    expect(events[0].action).toBe('suspend');
    expect(events[0].severity).toBe('warning');
  });

  it('should return feature flags', () => {
    let flags: any[] = [];
    service.getFeatureFlags().subscribe(f => (flags = f));

    const req = httpMock.expectOne('/api/v1/admin/features');
    expect(req.request.method).toBe('GET');
    req.flush({
      features: [{
        id: 'flag-001', name: 'Dark Mode', description: 'Dark theme',
        enabled: true, updated_at: '2026-01-01T00:00:00Z', metadata: { category: 'UI' },
      }],
    });

    expect(flags.length).toBe(1);
    expect(flags[0].name).toBe('Dark Mode');
    expect(flags[0].key).toBe('dark-mode');
    expect(flags[0].category).toBe('UI');
  });

  it('should return system health including database and redis', () => {
    let services: any[] = [];
    service.getSystemHealth().subscribe(s => (services = s));

    const req = httpMock.expectOne('/api/v1/admin/health/services');
    expect(req.request.method).toBe('GET');
    req.flush({
      timestamp: '2026-01-01T00:00:00Z',
      services: [
        { name: 'auth-service', status: 'healthy', latency_ms: 12 },
        { name: 'file-service', status: 'unhealthy', latency_ms: 0, message: 'timeout' },
      ],
      database: { status: 'healthy', latency_ms: 3 },
      redis: { status: 'healthy', latency_ms: 1 },
    });

    expect(services.length).toBe(4);
    expect(services[0].name).toBe('auth-service');
    expect(services[0].status).toBe('healthy');
    expect(services[1].status).toBe('down');
    expect(services.map(s => s.name)).toContain('postgres');
    expect(services.map(s => s.name)).toContain('redis');
  });

  it('should return announcements mapped from severity to priority', () => {
    let announcements: any[] = [];
    service.getAnnouncements().subscribe(a => (announcements = a));

    const req = httpMock.expectOne('/api/v1/admin/announcements');
    expect(req.request.method).toBe('GET');
    req.flush({
      announcements: [{
        id: 'ann-1', title: 'Maintenance', body: 'Scheduled downtime',
        severity: 'maintenance', status: 'published',
        created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
        target_audience: { role: 'all' },
      }],
    });

    expect(announcements.length).toBe(1);
    expect(announcements[0].priority).toBe('high');
    expect(announcements[0].content).toBe('Scheduled downtime');
  });

  it('should return analytics report', () => {
    let report: any;
    service.getAnalyticsReport().subscribe(r => (report = r));

    const req = httpMock.expectOne('/api/v1/admin/metrics/summary');
    expect(req.request.method).toBe('GET');
    req.flush(METRICS_RESPONSE);

    expect(report).toBeTruthy();
    expect(report.userSignups.length).toBe(3);
    expect(report.storageUsage.length).toBe(2);
    expect(report.activeUsers).toEqual([
      { label: 'Active', value: 30 },
      { label: 'Suspended', value: 2 },
    ]);
  });

  it('should toggle feature flag', () => {
    let flag: any;
    service.toggleFeatureFlag('flag-001', true).subscribe(f => (flag = f));

    const req = httpMock.expectOne('/api/v1/admin/features/flag-001');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ feature: { enabled: true } });
    req.flush({ id: 'flag-001', name: 'Dark Mode', enabled: true, updated_at: '2026-01-01T00:00:00Z' });

    expect(flag).toBeTruthy();
    expect(flag.enabled).toBeTrue();
  });

  it('should suspend a user', () => {
    let user: any;
    service.suspendUser('user-001').subscribe(u => (user = u));

    const req = httpMock.expectOne('/api/v1/admin/users/user-001/suspend');
    expect(req.request.method).toBe('PUT');
    req.flush({ ...USER_RESPONSE, status: 'suspended' });

    expect(user).toBeTruthy();
    expect(user.status).toBe('suspended');
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
