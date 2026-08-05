import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of } from 'rxjs';
import { AuditComponent } from './audit.component';
import { AdminApiService } from '../../core/services/admin-api.service';
import { AuditEvent } from '../../core/models/audit.model';

const MOCK_EVENTS: AuditEvent[] = [
  {
    id: 'aud-001', timestamp: '2026-04-17T12:30:00Z', userId: 'usr-001', userName: 'Alice Johnson',
    action: 'delete', resourceType: 'File', resourceName: 'old-backup.zip',
    details: 'Permanently deleted file from trash', ipAddress: '192.168.1.15', severity: 'warning',
  },
  {
    id: 'aud-002', timestamp: '2026-04-17T12:35:00Z', userId: 'usr-002', userName: 'Bob Martinez',
    action: 'create', resourceType: 'Folder', resourceName: 'Q2 Reports',
    details: 'Created new folder', ipAddress: '192.168.1.22', severity: 'info',
  },
  {
    id: 'aud-003', timestamp: '2026-04-17T12:40:00Z', userId: 'usr-003', userName: 'Carol Chen',
    action: 'suspend', resourceType: 'User', resourceName: 'dave@otterworks.io',
    details: 'Suspended user account', ipAddress: '192.168.1.30', severity: 'critical',
  },
  {
    id: 'aud-004', timestamp: '2026-04-17T12:45:00Z', userId: 'usr-001', userName: 'Alice Johnson',
    action: 'create', resourceType: 'Document', resourceName: 'roadmap.md',
    details: 'Created new document', ipAddress: '192.168.1.15', severity: 'info',
  },
];

describe('AuditComponent', () => {
  let component: AuditComponent;
  let fixture: ComponentFixture<AuditComponent>;
  let apiSpy: jasmine.SpyObj<AdminApiService>;

  const filteredData = () =>
    component.dataSource.data.filter(row => component.dataSource.filterPredicate(row, component.dataSource.filter));

  const search = (value: string) => {
    const event = { target: { value } } as unknown as Event;
    component.applyFilter(event);
  };

  beforeEach(async () => {
    apiSpy = jasmine.createSpyObj<AdminApiService>('AdminApiService', ['getAuditEvents']);
    apiSpy.getAuditEvents.and.returnValue(of(MOCK_EVENTS));

    await TestBed.configureTestingModule({
      imports: [AuditComponent, NoopAnimationsModule],
      providers: [{ provide: AdminApiService, useValue: apiSpy }],
    }).compileComponents();

    fixture = TestBed.createComponent(AuditComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should render events into the table', () => {
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.dataSource.data.length).toBe(MOCK_EVENTS.length);
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('tr[mat-row]');
    expect(rows.length).toBe(MOCK_EVENTS.length);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Alice Johnson');
  });

  it('should narrow results by search text', () => {
    fixture.detectChanges();
    search('alice');
    fixture.detectChanges();
    const results = filteredData();
    expect(results.length).toBe(2);
    expect(results.every(e => e.userName === 'Alice Johnson')).toBeTrue();
  });

  it('should narrow results by action filter', () => {
    fixture.detectChanges();
    component.actionFilter = 'create';
    component.applyFilters();
    fixture.detectChanges();
    const results = filteredData();
    expect(results.length).toBe(2);
    expect(results.every(e => e.action === 'create')).toBeTrue();
  });

  it('should narrow results by severity filter', () => {
    fixture.detectChanges();
    component.severityFilter = 'critical';
    component.applyFilters();
    fixture.detectChanges();
    const results = filteredData();
    expect(results.length).toBe(1);
    expect(results[0].id).toBe('aud-003');
  });

  it('should compose action and severity filters with search text', () => {
    fixture.detectChanges();
    search('alice');
    component.actionFilter = 'create';
    component.severityFilter = 'info';
    component.applyFilters();
    fixture.detectChanges();
    const results = filteredData();
    expect(results.length).toBe(1);
    expect(results[0].id).toBe('aud-004');
  });

  it('should restore the full list when filters are cleared', () => {
    fixture.detectChanges();
    search('alice');
    component.actionFilter = 'create';
    component.severityFilter = 'info';
    component.applyFilters();
    fixture.detectChanges();
    expect(filteredData().length).toBe(1);

    search('');
    component.actionFilter = '';
    component.severityFilter = '';
    component.applyFilters();
    fixture.detectChanges();
    expect(filteredData().length).toBe(MOCK_EVENTS.length);
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('tr[mat-row]');
    expect(rows.length).toBe(MOCK_EVENTS.length);
  });
});
