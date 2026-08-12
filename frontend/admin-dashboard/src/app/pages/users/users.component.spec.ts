import { ComponentFixture, TestBed, fakeAsync, flush } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { RouterTestingModule } from '@angular/router/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { UsersComponent } from './users.component';

describe('UsersComponent', () => {
  let component: UsersComponent;
  let fixture: ComponentFixture<UsersComponent>;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [
        UsersComponent,
        HttpClientTestingModule,
        RouterTestingModule,
        NoopAnimationsModule,
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(UsersComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
  });

  it('should load users', fakeAsync(() => {
    fixture.detectChanges();
    httpMock.expectOne('/api/v1/admin/users').flush({
      users: [{
        id: 'user-1',
        email: 'alice@example.com',
        display_name: 'Alice Example',
        role: 'admin',
        status: 'active',
        storage_quota: { used_bytes: 1024, quota_bytes: 4096 },
        last_login_at: '2024-01-02T03:04:05Z',
        created_at: '2024-01-01T03:04:05Z',
        metadata: { department: 'Engineering', documents_count: 3 },
      }],
    });
    fixture.detectChanges();
    flush();
    expect(component.loading).toBeFalse();
    expect(component.dataSource.data.length).toBeGreaterThan(0);
  }));

  it('should have correct displayed columns', () => {
    expect(component.displayedColumns).toEqual([
      'displayName', 'role', 'status', 'department', 'lastLogin', 'actions',
    ]);
  });

  it('should apply text filter', fakeAsync(() => {
    fixture.detectChanges();
    httpMock.expectOne('/api/v1/admin/users').flush({
      users: [{
        id: 'user-1',
        email: 'alice@example.com',
        display_name: 'Alice Example',
        role: 'admin',
        status: 'active',
        storage_quota: { used_bytes: 1024, quota_bytes: 4096 },
        last_login_at: '2024-01-02T03:04:05Z',
        created_at: '2024-01-01T03:04:05Z',
        metadata: { department: 'Engineering', documents_count: 3 },
      }],
    });
    fixture.detectChanges();
    flush();
    const event = { target: { value: 'alice' } } as unknown as Event;
    component.applyFilter(event);
    expect(component.dataSource.filter).toBe('alice');
  }));

  it('should display page title', fakeAsync(() => {
    fixture.detectChanges();
    httpMock.expectOne('/api/v1/admin/users').flush({ users: [] });
    fixture.detectChanges();
    flush();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('User Management');
  }));
});
