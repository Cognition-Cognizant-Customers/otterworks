import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AdminApiService } from './admin-api.service';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

describe('AdminApiService', () => {
  let service: AdminApiService;

  beforeEach(() => {
    TestBed.configureTestingModule({
    imports: [],
    providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()]
});
    service = TestBed.inject(AdminApiService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should return dashboard stats', fakeAsync(() => {
    service.getDashboardStats().subscribe(stats => {
      expect(stats).toBeTruthy();
      expect(stats.totalUsers).toBeGreaterThan(0);
      expect(stats.activeDocuments).toBeGreaterThan(0);
      expect(stats.storageUsed).toBeTruthy();
      expect(stats.activeSessions).toBeGreaterThan(0);
    });
    tick(700);
  }));

  it('should return users list', fakeAsync(() => {
    service.getUsers().subscribe(users => {
      expect(users).toBeTruthy();
      expect(users.length).toBeGreaterThan(0);
      expect(users[0].displayName).toBeTruthy();
      expect(users[0].email).toBeTruthy();
    });
    tick(700);
  }));

  it('should return a single user by id', fakeAsync(() => {
    service.getUser('user-001').subscribe(user => {
      expect(user).toBeTruthy();
      expect(user!.id).toBe('user-001');
    });
    tick(700);
  }));

  it('should return audit events', fakeAsync(() => {
    service.getAuditEvents().subscribe(events => {
      expect(events).toBeTruthy();
      expect(events.length).toBeGreaterThan(0);
      expect(events[0].action).toBeTruthy();
    });
    tick(700);
  }));

  it('should return feature flags', fakeAsync(() => {
    service.getFeatureFlags().subscribe(flags => {
      expect(flags).toBeTruthy();
      expect(flags.length).toBeGreaterThan(0);
      expect(flags[0].name).toBeTruthy();
      expect(flags[0].key).toBeTruthy();
    });
    tick(700);
  }));

  it('should return system health', fakeAsync(() => {
    service.getSystemHealth().subscribe(services => {
      expect(services).toBeTruthy();
      expect(services.length).toBeGreaterThan(0);
      expect(services[0].name).toBeTruthy();
      expect(services[0].status).toBeTruthy();
    });
    tick(700);
  }));

  it('should return announcements', fakeAsync(() => {
    service.getAnnouncements().subscribe(announcements => {
      expect(announcements).toBeTruthy();
      expect(announcements.length).toBeGreaterThan(0);
    });
    tick(700);
  }));

  it('should return analytics report', fakeAsync(() => {
    service.getAnalyticsReport().subscribe(report => {
      expect(report).toBeTruthy();
      expect(report.userSignups).toBeTruthy();
      expect(report.activeUsers).toBeTruthy();
      expect(report.storageUsage).toBeTruthy();
    });
    tick(700);
  }));

  it('should toggle feature flag', fakeAsync(() => {
    service.toggleFeatureFlag('flag-001', true).subscribe(flag => {
      expect(flag).toBeTruthy();
      expect(flag.enabled).toBeTrue();
    });
    tick(700);
  }));

  it('should suspend a user', fakeAsync(() => {
    service.suspendUser('user-001').subscribe(user => {
      expect(user).toBeTruthy();
      expect(user.status).toBe('suspended');
    });
    tick(700);
  }));

  describe('Incident API methods', () => {
    let httpMock: HttpTestingController;

    beforeEach(() => {
      httpMock = TestBed.inject(HttpTestingController);
    });

    afterEach(() => {
      httpMock.verify();
    });

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
