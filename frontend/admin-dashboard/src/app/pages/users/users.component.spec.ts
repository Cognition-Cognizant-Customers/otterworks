import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { RouterTestingModule } from '@angular/router/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { UsersComponent } from './users.component';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

const USERS_RESPONSE = {
  users: [
    {
      id: 'user-001', email: 'alice@otterworks.io', display_name: 'Alice',
      role: 'admin', status: 'active', created_at: '2026-01-01T00:00:00Z',
      last_login_at: '2026-01-02T00:00:00Z',
      storage_quota: { used_bytes: 1024, quota_bytes: 2048 },
      metadata: { department: 'Engineering', documents_count: 5 },
    },
    {
      id: 'user-002', email: 'bob@otterworks.io', display_name: 'Bob',
      role: 'viewer', status: 'suspended', created_at: '2026-01-01T00:00:00Z',
      last_login_at: '2026-01-03T00:00:00Z',
      storage_quota: { used_bytes: 512, quota_bytes: 2048 },
      metadata: { department: 'Sales', documents_count: 2 },
    },
  ],
};

describe('UsersComponent', () => {
  let component: UsersComponent;
  let fixture: ComponentFixture<UsersComponent>;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UsersComponent, RouterTestingModule, NoopAnimationsModule],
      providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(UsersComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  function flushUsersRequest(): void {
    httpMock.expectOne('/api/v1/admin/users').flush(USERS_RESPONSE);
  }

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
  });

  it('should load users', () => {
    fixture.detectChanges();
    flushUsersRequest();
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.dataSource.data.length).toBeGreaterThan(0);
  });

  it('should have correct displayed columns', () => {
    expect(component.displayedColumns).toEqual([
      'displayName', 'role', 'status', 'department', 'lastLogin', 'actions',
    ]);
  });

  it('should apply text filter', () => {
    fixture.detectChanges();
    flushUsersRequest();
    fixture.detectChanges();
    const event = { target: { value: 'alice' } } as unknown as Event;
    component.applyFilter(event);
    expect(component.dataSource.filter).toBe('alice');
  });

  it('should display page title', () => {
    fixture.detectChanges();
    flushUsersRequest();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('User Management');
  });
});
