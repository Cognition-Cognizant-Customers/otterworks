import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { HealthComponent } from './health.component';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

const HEALTH_RESPONSE = {
  timestamp: '2026-01-01T00:00:00Z',
  services: [
    { name: 'auth-service', status: 'healthy', latency_ms: 12 },
    { name: 'file-service', status: 'unhealthy', latency_ms: 0, message: 'timeout' },
    { name: 'document-service', status: 'degraded', latency_ms: 450 },
  ],
  database: { status: 'healthy', latency_ms: 3 },
  redis: { status: 'healthy', latency_ms: 1 },
};

describe('HealthComponent', () => {
  let component: HealthComponent;
  let fixture: ComponentFixture<HealthComponent>;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HealthComponent, NoopAnimationsModule],
      providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(HealthComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  function flushHealthRequest(): void {
    httpMock.expectOne('/api/v1/admin/health/services').flush(HEALTH_RESPONSE);
  }

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
  });

  it('should load system health data', () => {
    fixture.detectChanges();
    flushHealthRequest();
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.services.length).toBeGreaterThan(0);
  });

  it('should compute health counts', () => {
    fixture.detectChanges();
    flushHealthRequest();
    fixture.detectChanges();
    const total = component.healthyCounts.healthy +
                  component.healthyCounts.degraded +
                  component.healthyCounts.down;
    expect(total).toBe(component.services.length);
  });

  it('should set error state when health request fails', () => {
    fixture.detectChanges();
    httpMock.expectOne('/api/v1/admin/health/services')
      .flush({ error: 'unavailable' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(component.error).toBeTrue();
    expect(component.loading).toBeFalse();
    expect(component.services.length).toBe(0);
  });

  it('should display page title', () => {
    fixture.detectChanges();
    flushHealthRequest();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('System Health');
  });
});
