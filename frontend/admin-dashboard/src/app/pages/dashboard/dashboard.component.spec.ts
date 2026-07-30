import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { DashboardComponent } from './dashboard.component';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

const METRICS_RESPONSE = {
  users: { total: 42, active: 30, suspended: 2, by_role: { admin: 2, editor: 20, viewer: 20 } },
  storage: { total_used_bytes: 5 * 1024 ** 3, by_tier: { free: 30, pro: 12 } },
  audit: { total_events: 100, top_actions: { 'user.login': 60, 'file.upload': 40 } },
};

describe('DashboardComponent', () => {
  let component: DashboardComponent;
  let fixture: ComponentFixture<DashboardComponent>;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DashboardComponent, NoopAnimationsModule],
      providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(DashboardComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  // ngOnInit issues two GETs to /admin/metrics/summary (stats + analytics report)
  function flushInitRequests(): void {
    const reqs = httpMock.match('/api/v1/admin/metrics/summary');
    reqs.forEach(req => req.flush(METRICS_RESPONSE));
  }

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
    expect(component.stats).toBeNull();
  });

  it('should load dashboard stats', () => {
    fixture.detectChanges();
    flushInitRequests();
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.stats).toBeTruthy();
    expect(component.stats!.totalUsers).toBeGreaterThan(0);
  });

  it('should display stat cards when loaded', () => {
    fixture.detectChanges();
    flushInitRequests();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const statCards = compiled.querySelectorAll('.stat-card');
    expect(statCards.length).toBe(4);
  });

  it('should show page title', () => {
    fixture.detectChanges();
    flushInitRequests();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Dashboard');
  });
});
