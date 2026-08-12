import { ComponentFixture, TestBed, fakeAsync } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { HealthComponent } from './health.component';

describe('HealthComponent', () => {
  let component: HealthComponent;
  let fixture: ComponentFixture<HealthComponent>;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [
        HealthComponent,
        HttpClientTestingModule,
        NoopAnimationsModule,
      ],
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
    httpMock.expectOne('/api/v1/admin/health/services').flush({
      services: [
        { name: 'auth-service', status: 'healthy', latency_ms: 12, message: 'Ready' },
        { name: 'search-service', status: 'degraded', latency_ms: 250, message: 'Slow' },
      ],
      timestamp: '2024-01-02T03:04:05Z',
      database: { status: 'healthy', latency_ms: 2, message: 'Ready' },
      redis: { status: 'healthy', latency_ms: 1, message: 'Ready' },
    });
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.services.length).toBeGreaterThan(0);
  }));

  it('should compute health counts', fakeAsync(() => {
    fixture.detectChanges();
    httpMock.expectOne('/api/v1/admin/health/services').flush({
      services: [
        { name: 'auth-service', status: 'healthy', latency_ms: 12, message: 'Ready' },
        { name: 'search-service', status: 'degraded', latency_ms: 250, message: 'Slow' },
      ],
      timestamp: '2024-01-02T03:04:05Z',
      database: { status: 'healthy', latency_ms: 2, message: 'Ready' },
      redis: { status: 'healthy', latency_ms: 1, message: 'Ready' },
    });
    fixture.detectChanges();
    const total = component.healthyCounts.healthy +
                  component.healthyCounts.degraded +
                  component.healthyCounts.down;
    expect(total).toBe(component.services.length);
  }));

  it('should display page title', fakeAsync(() => {
    fixture.detectChanges();
    httpMock.expectOne('/api/v1/admin/health/services').flush({ services: [] });
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('System Health');
  }));
});
