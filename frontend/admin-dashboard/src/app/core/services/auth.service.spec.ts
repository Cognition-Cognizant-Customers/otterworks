import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { RouterTestingModule } from '@angular/router/testing';
import { Router } from '@angular/router';
import { AuthService, AuthUser } from './auth.service';

describe('AuthService', () => {
  let service: AuthService;
  let router: Router;
  let httpTestingController: HttpTestingController;

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

  function createToken(roles: string[]): string {
    const encode = (value: object): string =>
      btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    return `${encode({ alg: 'none', typ: 'JWT' })}.${encode({ roles })}.`;
  }

  function flushLogin(roles: string[] = ['ADMIN']): string {
    const accessToken = createToken(roles);
    const request = httpTestingController.expectOne('/api/v1/auth/login');
    request.flush({
      accessToken,
      refreshToken: 'refresh-token',
      tokenType: 'Bearer',
      expiresIn: 3600,
      user: {
        id: 'user-id',
        email: 'admin@otterworks.io',
        displayName: 'Admin User',
        avatarUrl: null,
      },
    });
    return accessToken;
  }

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should not be authenticated initially', () => {
    expect(service.isAuthenticated).toBeFalse();
    expect(service.currentUser).toBeNull();
  });

  it('should login successfully with valid credentials', () => {
    let loggedInUser;
    service.login('admin@otterworks.io', 'admin123').subscribe(user => {
      loggedInUser = user;
    });
    const accessToken = flushLogin();
    expect(loggedInUser).toBeTruthy();
    expect(loggedInUser!.email).toBe('admin@otterworks.io');
    expect(loggedInUser!.role).toBe('admin');
    expect(loggedInUser!.token).toBe(accessToken);
    expect(service.isAuthenticated).toBeTrue();
    expect(service.currentUser).toBeTruthy();
  });

  it('should store token and user in localStorage after login', () => {
    service.login('admin@otterworks.io', 'admin123').subscribe();
    const accessToken = flushLogin();
    expect(localStorage.getItem('ow_admin_token')).toBe(accessToken);
    expect(JSON.parse(localStorage.getItem('ow_admin_user')!)).toEqual(service.currentUser);
  });

  it('should reject non-admin users', () => {
    let error: Error | undefined;
    service.login('user@otterworks.io', 'user123').subscribe({
      error: (e: Error) => { error = e; },
    });
    flushLogin(['USER']);
    expect(error?.message).toBe('Insufficient privileges: admin access required');
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_user')).toBeNull();
    expect(service.currentUser).toBeNull();
  });

  it('should map HTTP 401 to invalid credentials', () => {
    let error: Error | undefined;
    service.login('admin@otterworks.io', 'wrong').subscribe({
      error: (e: Error) => { error = e; },
    });
    const request = httpTestingController.expectOne('/api/v1/auth/login');
    request.flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(error?.message).toBe('Invalid credentials');
  });

  it('should clear auth state on logout', () => {
    service.login('admin@otterworks.io', 'admin123').subscribe();
    flushLogin();
    spyOn(router, 'navigate');
    service.logout();
    expect(service.isAuthenticated).toBeFalse();
    expect(service.currentUser).toBeNull();
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(router.navigate).toHaveBeenCalledWith(['/login']);
  });

  it('should emit user on currentUser$ observable', () => {
    const emitted: (AuthUser | null)[] = [];
    service.currentUser$.subscribe(user => emitted.push(user));
    service.login('admin@otterworks.io', 'admin123').subscribe();
    flushLogin();
    expect(emitted.length).toBeGreaterThanOrEqual(2);
    expect(emitted[emitted.length - 1]).toBeTruthy();
  });

  it('should reject login with empty password', () => {
    let error: Error | undefined;
    service.login('admin@otterworks.io', '').subscribe({
      error: (e: Error) => { error = e; },
    });
    expect(error).toBeTruthy();
    expect(error!.message).toBe('Invalid credentials');
    httpTestingController.expectNone('/api/v1/auth/login');
  });
});
