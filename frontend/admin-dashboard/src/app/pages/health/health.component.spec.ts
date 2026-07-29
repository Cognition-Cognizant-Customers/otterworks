import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { HealthComponent } from './health.component';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

describe('HealthComponent', () => {
  let component: HealthComponent;
  let fixture: ComponentFixture<HealthComponent>;
  let httpMock: HttpTestingController;

  const healthResponse = {
    services: [
      { name: 'auth-service', status: 'healthy', latency_ms: 12 },
      { name: 'file-service', status: 'unhealthy', latency_ms: 0, message: 'connection refused' },
    ],
    database: { status: 'healthy', latency_ms: 2 },
    redis: { status: 'healthy', latency_ms: 1 },
    timestamp: '2026-01-01T00:00:00Z',
  };

  function flushHealth(): void {
    httpMock.match('/api/v1/admin/health/services').forEach(req => req.flush(healthResponse));
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
    imports: [HealthComponent,
        NoopAnimationsModule],
    providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()]
}).compileComponents();

    fixture = TestBed.createComponent(HealthComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
  });

  it('should load system health data', fakeAsync(() => {
    fixture.detectChanges();
    flushHealth();
    tick(700);
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.services.length).toBeGreaterThan(0);
  }));

  it('should compute health counts', fakeAsync(() => {
    fixture.detectChanges();
    flushHealth();
    tick(700);
    fixture.detectChanges();
    const total = component.healthyCounts.healthy +
                  component.healthyCounts.degraded +
                  component.healthyCounts.down;
    expect(total).toBe(component.services.length);
  }));

  it('should display page title', fakeAsync(() => {
    fixture.detectChanges();
    flushHealth();
    tick(700);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('System Health');
  }));
});
