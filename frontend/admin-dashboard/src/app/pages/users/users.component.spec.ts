import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { RouterTestingModule } from '@angular/router/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of } from 'rxjs';
import { UsersComponent } from './users.component';
import { AdminApiService } from '../../core/services/admin-api.service';
import { User } from '../../core/models/user.model';

const mockUsers: User[] = [
  {
    id: 'u1',
    email: 'alice@otterworks.io',
    displayName: 'Alice Otter',
    role: 'admin',
    status: 'active',
    storageUsed: 1024,
    storageQuota: 10240,
    lastLogin: new Date().toISOString(),
    createdAt: new Date().toISOString(),
    department: 'Engineering',
    documentsCount: 12,
  },
  {
    id: 'u2',
    email: 'bob@otterworks.io',
    displayName: 'Bob Beaver',
    role: 'viewer',
    status: 'suspended',
    storageUsed: 512,
    storageQuota: 10240,
    lastLogin: new Date().toISOString(),
    createdAt: new Date().toISOString(),
    department: 'Sales',
    documentsCount: 3,
  },
];

describe('UsersComponent', () => {
  let component: UsersComponent;
  let fixture: ComponentFixture<UsersComponent>;
  let api: jasmine.SpyObj<AdminApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj<AdminApiService>('AdminApiService', [
      'getUsers',
      'suspendUser',
      'restoreUser',
      'deleteUser',
    ]);
    api.getUsers.and.returnValue(of(mockUsers));

    await TestBed.configureTestingModule({
      imports: [UsersComponent, RouterTestingModule, NoopAnimationsModule],
      providers: [{ provide: AdminApiService, useValue: api }],
    }).compileComponents();

    fixture = TestBed.createComponent(UsersComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading state', () => {
    expect(component.loading).toBeTrue();
  });

  it('should load users', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
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
    tick(700);
    fixture.detectChanges();
    const event = { target: { value: 'alice' } } as unknown as Event;
    component.applyFilter(event);
    expect(component.dataSource.filter).toBe('alice');
  }));

  it('should display page title', fakeAsync(() => {
    fixture.detectChanges();
    tick(700);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('User Management');
  }));
});
