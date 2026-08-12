import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of } from 'rxjs';
import { HealthComponent } from './health.component';
import { AdminApiService } from '../../core/services/admin-api.service';
import { ServiceHealth } from '../../core/models/system-health.model';

const mockServices: ServiceHealth[] = [
  {
    name: 'auth-service',
    status: 'healthy',
    uptime: '99.99%',
    responseTime: 42,
    lastChecked: new Date().toISOString(),
    version: '3.1.0',
    port: 8081,
    language: 'Java 17',
  },
  {
    name: 'file-service',
    status: 'degraded',
    uptime: '99.20%',
    responseTime: 310,
    lastChecked: new Date().toISOString(),
    version: '1.8.2',
    port: 8082,
    language: 'Rust 1.77',
  },
  {
    name: 'search-service',
    status: 'down',
    uptime: '90.00%',
    responseTime: 0,
    lastChecked: new Date().toISOString(),
    version: '1.0.0',
    port: 8087,
    language: 'Python 3.12',
  },
];

describe('HealthComponent', () => {
  let component: HealthComponent;
  let fixture: ComponentFixture<HealthComponent>;
  let api: jasmine.SpyObj<AdminApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj<AdminApiService>('AdminApiService', ['getSystemHealth']);
    api.getSystemHealth.and.returnValue(of(mockServices));

    await TestBed.configureTestingModule({
      imports: [HealthComponent, NoopAnimationsModule],
      providers: [{ provide: AdminApiService, useValue: api }],
    }).compileComponents();

    fixture = TestBed.createComponent(HealthComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
  });

  it('should load system health data', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.services.length).toBeGreaterThan(0);
  }));

  it('should compute health counts', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    const total = component.healthyCounts.healthy +
                  component.healthyCounts.degraded +
                  component.healthyCounts.down;
    expect(total).toBe(component.services.length);
  }));

  it('should display page title', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('System Health');
  }));
});
