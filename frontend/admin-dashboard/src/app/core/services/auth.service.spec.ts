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

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should not be authenticated initially', () => {
    expect(service.isAuthenticated).toBeFalse();
    expect(service.currentUser).toBeNull();
  });

  it('should login successfully with an admin token', () => {
    let loggedInUser: AuthUser | undefined;
    service.login('admin@otterworks.io', 'test-password').subscribe(user => {
      loggedInUser = user;
    });
    const request = httpTestingController.expectOne('/api/v1/auth/login');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ email: 'admin@otterworks.io', password: 'test-password' });
    request.flush(loginResponse(unsignedToken(['USER', 'ADMIN'])));
    expect(loggedInUser).toBeTruthy();
    expect(loggedInUser!.email).toBe('admin@otterworks.io');
    expect(loggedInUser!.role).toBe('admin');
    expect(service.isAuthenticated).toBeTrue();
    expect(service.currentUser).toBeTruthy();
  });

  it('should store token in localStorage after login', () => {
    const token = unsignedToken(['ADMIN']);
    service.login('admin@otterworks.io', 'test-password').subscribe();
    httpTestingController.expectOne('/api/v1/auth/login').flush(loginResponse(token));
    expect(localStorage.getItem('ow_admin_token')).toBe(token);
    expect(localStorage.getItem('ow_admin_user')).toBeTruthy();
  });

  it('should clear auth state on logout', () => {
    service.login('admin@otterworks.io', 'test-password').subscribe();
    httpTestingController.expectOne('/api/v1/auth/login').flush(loginResponse(unsignedToken(['ADMIN'])));
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
    service.login('admin@otterworks.io', 'test-password').subscribe();
    httpTestingController.expectOne('/api/v1/auth/login').flush(loginResponse(unsignedToken(['ADMIN'])));
    expect(emitted.length).toBeGreaterThanOrEqual(2);
    expect(emitted[emitted.length - 1]).toBeTruthy();
  });

  it('should return token from getToken()', () => {
    expect(service.getToken()).toBeNull();
    const token = unsignedToken(['ADMIN']);
    service.login('admin@otterworks.io', 'test-password').subscribe();
    httpTestingController.expectOne('/api/v1/auth/login').flush(loginResponse(token));
    expect(service.getToken()).toBe(token);
  });

  it('should fail without returning or storing a token when the HTTP call errors', () => {
    let error: Error | undefined;
    service.login('admin@otterworks.io', 'wrong-password').subscribe({
      error: (e: Error) => { error = e; },
    });
    httpTestingController.expectOne('/api/v1/auth/login').flush('Invalid credentials', {
      status: 401,
      statusText: 'Unauthorized',
    });
    expect(error).toBeTruthy();
    expect(error!.message).toBe('Invalid credentials');
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_user')).toBeNull();
    expect(service.currentUser).toBeNull();
  });

  it('should map non-401 HTTP failures to a generic login error', () => {
    let error: Error | undefined;
    service.login('admin@otterworks.io', 'test-password').subscribe({
      error: (e: Error) => { error = e; },
    });
    httpTestingController.expectOne('/api/v1/auth/login').flush('Server error', {
      status: 500,
      statusText: 'Internal Server Error',
    });
    expect(error!.message).toBe('Login failed. Please try again.');
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_user')).toBeNull();
  });

  it('should map a 400 HTTP failure to invalid credentials', () => {
    let error: Error | undefined;
    service.login('admin@otterworks.io', 'test-password').subscribe({
      error: (e: Error) => { error = e; },
    });
    httpTestingController.expectOne('/api/v1/auth/login').flush('Invalid credentials', {
      status: 400,
      statusText: 'Bad Request',
    });
    expect(error!.message).toBe('Invalid credentials');
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_user')).toBeNull();
  });

  it('should reject non-admin tokens without storing auth state', () => {
    let error: Error | undefined;
    service.login('user@otterworks.io', 'user-password').subscribe({
      error: (e: Error) => { error = e; },
    });
    httpTestingController.expectOne('/api/v1/auth/login').flush(loginResponse(unsignedToken(['USER'])));
    expect(error!.message).toBe('Insufficient privileges');
    expect(localStorage.getItem('ow_admin_token')).toBeNull();
    expect(localStorage.getItem('ow_admin_user')).toBeNull();
    expect(service.currentUser).toBeNull();
  });
});

function loginResponse(accessToken: string) {
  return {
    accessToken,
    refreshToken: 'refresh-token',
    tokenType: 'Bearer',
    expiresIn: 3600,
    user: {
      id: 'user-1',
      email: 'admin@otterworks.io',
      displayName: 'Admin User',
      avatarUrl: null,
    },
  };
}

function unsignedToken(roles: string[]): string {
  const encode = (value: object): string => btoa(JSON.stringify(value))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `${encode({ alg: 'none', typ: 'JWT' })}.${encode({ roles })}.dummy-signature`;
}
