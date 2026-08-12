import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Subject, of } from 'rxjs';
import { DashboardComponent } from './dashboard.component';
import { AdminApiService } from '../../core/services/admin-api.service';
import { DashboardStats, AnalyticsReport } from '../../core/models/analytics.model';

const mockStats: DashboardStats = {
  totalUsers: 42,
  activeDocuments: 128,
  storageUsed: '1.2 TB',
  activeSessions: 7,
  usersGrowth: 3.2,
  documentsGrowth: 1.4,
  storageGrowth: 0.8,
  sessionsGrowth: 2.1,
};

const mockReport: AnalyticsReport = {
  userSignups: [{ label: 'Mon', value: 3 }],
  storageUsage: [{ label: 'Mon', value: 10 }],
  documentActivity: [{ label: 'Mon', value: 5 }],
  activeUsers: [{ label: 'Mon', value: 12 }],
  topFileTypes: [{ label: 'pdf', value: 6 }],
  peakHours: [{ label: '10:00', value: 9 }],
};

describe('DashboardComponent', () => {
  let component: DashboardComponent;
  let fixture: ComponentFixture<DashboardComponent>;
  let api: jasmine.SpyObj<AdminApiService> & { statsChanged$: Subject<void> };

  beforeEach(async () => {
    api = Object.assign(
      jasmine.createSpyObj<AdminApiService>('AdminApiService', [
        'getDashboardStats',
        'getAnalyticsReport',
      ]),
      { statsChanged$: new Subject<void>() },
    );
    api.getDashboardStats.and.returnValue(of(mockStats));
    api.getAnalyticsReport.and.returnValue(of(mockReport));

    await TestBed.configureTestingModule({
      imports: [DashboardComponent, NoopAnimationsModule],
      providers: [{ provide: AdminApiService, useValue: api }],
    }).compileComponents();

    fixture = TestBed.createComponent(DashboardComponent);
    component = fixture.componentInstance;
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
    tick(700);
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.stats).toBeTruthy();
    expect(component.stats!.totalUsers).toBeGreaterThan(0);
  }));

  it('should display stat cards when loaded', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const statCards = compiled.querySelectorAll('.stat-card');
    expect(statCards.length).toBe(4);
  }));

  it('should show page title', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('Dashboard');
  }));
});
