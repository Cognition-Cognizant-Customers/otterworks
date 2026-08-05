import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { RouterTestingModule } from '@angular/router/testing';
import { Router } from '@angular/router';
import { AuthService, AuthUser } from './auth.service';

describe('AuthService', () => {
  let service: AuthService;
  let router: Router;
  let httpTestingController: HttpTestingController;

  const loginResponse = (accessToken = 'access-token', refreshToken = 'refresh-token') => ({
    accessToken, refreshToken, tokenType: 'Bearer', expiresIn: 3600,
    user: { id: 'user-id', email: 'admin@otterworks.io', displayName: 'Admin User', avatarUrl: null },
  });

  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule, RouterTestingModule],
    });
    service = TestBed.inject(AuthService);
    router = TestBed.inject(Router);
    httpTestingController = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpTestingController.verify();
    localStorage.clear();
  });

  function completeLogin(roles: string[] = ['ADMIN']): void {
    const login = httpTestingController.expectOne('/api/v1/auth/login');
    login.flush(loginResponse());
    const profile = httpTestingController.expectOne('/api/v1/auth/profile');
    expect(profile.request.headers.get('Authorization')).toBe('Bearer access-token');
    profile.flush({
      id: 'user-id', email: 'admin@otterworks.io', displayName: 'Admin User',
      avatarUrl: null, roles,
    });
  }

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should not be authenticated initially', () => {
    expect(service.isAuthenticated).toBeFalse();
    expect(service.currentUser).toBeNull();
  });

  it('should login and verify the role through the profile endpoint', () => {
    let loggedInUser: AuthUser | undefined;
    service.login('admin@otterworks.io', 'admin123').subscribe(user => loggedInUser = user);
    completeLogin();
    expect(loggedInUser?.role).toBe('admin');
    expect(service.isAuthenticated).toBeTrue();
    expect(localStorage.getItem('ow_admin_token')).toBe('access-token');
    expect(localStorage.getItem('ow_admin_refresh_token')).toBe('refresh-token');
  });

  it('should reject non-admin users without persisting the session', () => {
    let error: Error | undefined;
    service.login('user@otterworks.io', 'user123').subscribe({
      error: (err: Error) => error = err,
    });
    completeLogin(['USER']);
    expect(error?.message).toBe('Insufficient privileges: admin access required');
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_refresh_token')).toBeNull();
    expect(service.currentUser).toBeNull();
  });

  it('should map login HTTP 401 to invalid credentials', () => {
    let error: Error | undefined;
    service.login('admin@otterworks.io', 'wrong').subscribe({
      error: (err: Error) => error = err,
    });
    httpTestingController.expectOne('/api/v1/auth/login')
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(error?.message).toBe('Invalid credentials');
  });

  it('should refresh the session and rotate both tokens', () => {
    localStorage.setItem('ow_admin_token', 'old-access');
    localStorage.setItem('ow_admin_refresh_token', 'old-refresh');
    localStorage.setItem('ow_admin_user', JSON.stringify({
      id: 'user-id', email: 'admin@otterworks.io', displayName: 'Admin User',
      role: 'admin', token: 'old-access',
    }));
    let token: string | undefined;
    service.refreshSession().subscribe(value => token = value);
    const request = httpTestingController.expectOne('/api/v1/auth/refresh');
    expect(request.request.headers.get('Authorization')).toBe('Bearer old-refresh');
    expect(request.request.body).toEqual({});
    request.flush(loginResponse('new-access', 'new-refresh'));
    const profile = httpTestingController.expectOne('/api/v1/auth/profile');
    expect(profile.request.headers.get('Authorization')).toBe('Bearer new-access');
    profile.flush({
      id: 'user-id', email: 'admin@otterworks.io', displayName: 'Admin User',
      avatarUrl: null, roles: ['ADMIN'],
    });
    expect(token).toBe('new-access');
    expect(localStorage.getItem('ow_admin_token')).toBe('new-access');
    expect(localStorage.getItem('ow_admin_refresh_token')).toBe('new-refresh');
    expect(service.currentUser?.token).toBe('new-access');
  });

  it('should not restore a session when logout happens during refresh', () => {
    localStorage.setItem('ow_admin_token', 'old-access');
    localStorage.setItem('ow_admin_refresh_token', 'old-refresh');
    localStorage.setItem('ow_admin_user', JSON.stringify({
      id: 'user-id', email: 'admin@otterworks.io', displayName: 'Admin User',
      role: 'admin', token: 'old-access',
    }));
    service.refreshSession().subscribe();
    const refresh = httpTestingController.expectOne('/api/v1/auth/refresh');
    service.logout();
    refresh.flush(loginResponse('new-access', 'new-refresh'));
    const profile = httpTestingController.expectOne('/api/v1/auth/profile');
    profile.flush({
      id: 'user-id', email: 'admin@otterworks.io', displayName: 'Admin User',
      avatarUrl: null, roles: ['ADMIN'],
    });
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_refresh_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_user')).toBeNull();
    expect(service.currentUser).toBeNull();
  });

  it('should not restore a session when logout happens during login', () => {
    service.login('admin@otterworks.io', 'admin123').subscribe();
    const login = httpTestingController.expectOne('/api/v1/auth/login');
    service.logout();
    login.flush(loginResponse());
    const profile = httpTestingController.expectOne('/api/v1/auth/profile');
    profile.flush({
      id: 'user-id', email: 'admin@otterworks.io', displayName: 'Admin User',
      avatarUrl: null, roles: ['ADMIN'],
    });
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_refresh_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_user')).toBeNull();
    expect(service.currentUser).toBeNull();
  });

  it('should share one in-flight refresh request', () => {
    localStorage.setItem('ow_admin_refresh_token', 'old-refresh');
    const tokens: string[] = [];
    service.refreshSession().subscribe(token => tokens.push(token));
    service.refreshSession().subscribe(token => tokens.push(token));
    const requests = httpTestingController.match('/api/v1/auth/refresh');
    expect(requests.length).toBe(1);
    requests[0].flush(loginResponse('new-access', 'new-refresh'));
    const profiles = httpTestingController.match('/api/v1/auth/profile');
    expect(profiles.length).toBe(1);
    profiles[0].flush({
      id: 'user-id', email: 'admin@otterworks.io', displayName: 'Admin User',
      avatarUrl: null, roles: ['ADMIN'],
    });
    expect(tokens).toEqual(['new-access', 'new-access']);
  });

  it('should clear the session when refresh fails', () => {
    localStorage.setItem('ow_admin_token', 'old-access');
    localStorage.setItem('ow_admin_refresh_token', 'old-refresh');
    spyOn(router, 'navigate');
    let error: Error | undefined;
    service.refreshSession().subscribe({ error: (err: Error) => error = err });
    httpTestingController.expectOne('/api/v1/auth/refresh')
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(error).toBeTruthy();
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_refresh_token')).toBeNull();
    expect(router.navigate).toHaveBeenCalledWith(['/login']);
  });

  it('should clear auth state on logout', () => {
    localStorage.setItem('ow_admin_token', 'access-token');
    localStorage.setItem('ow_admin_refresh_token', 'refresh-token');
    spyOn(router, 'navigate');
    service.logout();
    expect(service.isAuthenticated).toBeFalse();
    expect(service.currentUser).toBeNull();
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_refresh_token')).toBeNull();
    expect(router.navigate).toHaveBeenCalledWith(['/login']);
  });

  it('should emit user on currentUser$ observable', () => {
    const emitted: (AuthUser | null)[] = [];
    service.currentUser$.subscribe(user => emitted.push(user));
    service.login('admin@otterworks.io', 'admin123').subscribe();
    completeLogin();
    expect(emitted.length).toBeGreaterThanOrEqual(2);
    expect(emitted[emitted.length - 1]).toBeTruthy();
  });

  it('should reject login with empty password', () => {
    let error: Error | undefined;
    service.login('admin@otterworks.io', '').subscribe({
      error: (err: Error) => error = err,
    });
    expect(error?.message).toBe('Invalid credentials');
    httpTestingController.expectNone('/api/v1/auth/login');
  });
});
