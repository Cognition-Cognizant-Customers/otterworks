import { ComponentFixture, TestBed, fakeAsync } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { DashboardComponent } from './dashboard.component';

describe('DashboardComponent', () => {
  let component: DashboardComponent;
  let fixture: ComponentFixture<DashboardComponent>;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [
        DashboardComponent,
        HttpClientTestingModule,
        NoopAnimationsModule,
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(DashboardComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
    expect(component.stats).toBeNull();
  });

  it('should load dashboard stats', fakeAsync(() => {
    fixture.detectChanges();
    const requests = httpMock.match('/api/v1/admin/metrics/summary');
    expect(requests.length).toBe(2);
    requests.forEach(request => request.flush({
      users: { total: 12, active: 9, suspended: 3, by_role: { admin: 2, editor: 6, viewer: 4 } },
      storage: { total_used_bytes: 1024 * 1024 * 128, by_tier: { standard: 12 } },
      audit: { total_events: 24, top_actions: { login: 18, update: 6 } },
    }));
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.stats).toBeTruthy();
    expect(component.stats!.totalUsers).toBeGreaterThan(0);
  }));

  it('should display stat cards when loaded', fakeAsync(() => {
    fixture.detectChanges();
    httpMock.match('/api/v1/admin/metrics/summary').forEach(request => request.flush({
      users: { total: 12, active: 9, suspended: 3, by_role: { admin: 2 } },
      storage: { total_used_bytes: 1024, by_tier: { standard: 12 } },
      audit: { total_events: 24, top_actions: { login: 18 } },
    }));
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const statCards = compiled.querySelectorAll('.stat-card');
    expect(statCards.length).toBe(4);
  }));

  it('should show page title', fakeAsync(() => {
    fixture.detectChanges();
    httpMock.match('/api/v1/admin/metrics/summary').forEach(request => request.flush({
      users: { total: 0, active: 0, suspended: 0, by_role: {} },
      storage: { total_used_bytes: 0, by_tier: {} },
      audit: { total_events: 0, top_actions: {} },
    }));
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Dashboard');
  }));
});
