import { TestBed } from '@angular/core/testing';
import { HttpClient, HttpClientModule, HTTP_INTERCEPTORS } from '@angular/common/http';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { RouterTestingModule } from '@angular/router/testing';
import { of, throwError } from 'rxjs';
import { JwtInterceptor } from './jwt.interceptor';
import { AuthService } from '../services/auth.service';

describe('JwtInterceptor', () => {
  let http: HttpClient;
  let httpTestingController: HttpTestingController;
  let authService: jasmine.SpyObj<AuthService>;

  beforeEach(() => {
    authService = jasmine.createSpyObj<AuthService>('AuthService', [
      'getToken', 'getRefreshToken', 'refreshSession', 'logout',
    ]);
    authService.getToken.and.returnValue('old-token');
    authService.getRefreshToken.and.returnValue('refresh-token');
    TestBed.configureTestingModule({
      imports: [HttpClientModule, HttpClientTestingModule, RouterTestingModule],
      providers: [
        { provide: AuthService, useValue: authService },
        { provide: HTTP_INTERCEPTORS, useClass: JwtInterceptor, multi: true },
      ],
    });
    http = TestBed.inject(HttpClient);
    httpTestingController = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTestingController.verify());

  it('should refresh once and retry a 401 with the new token', () => {
    authService.refreshSession.and.returnValue(of('new-token'));
    let result: unknown;
    http.get('/api/v1/admin/data').subscribe(value => result = value);
    const initial = httpTestingController.expectOne('/api/v1/admin/data');
    expect(initial.request.headers.get('Authorization')).toBe('Bearer old-token');
    initial.flush({}, { status: 401, statusText: 'Unauthorized' });
    const retry = httpTestingController.expectOne('/api/v1/admin/data');
    expect(retry.request.headers.get('Authorization')).toBe('Bearer new-token');
    retry.flush({ ok: true });
    expect(result).toEqual({ ok: true });
    expect(authService.refreshSession).toHaveBeenCalledTimes(1);
  });

  it('should propagate retried request failures without logging out', () => {
    authService.refreshSession.and.returnValue(of('new-token'));
    let error: unknown;
    http.get('/api/v1/admin/data').subscribe({ error: err => error = err });
    httpTestingController.expectOne('/api/v1/admin/data')
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    httpTestingController.expectOne('/api/v1/admin/data')
      .flush({}, { status: 500, statusText: 'Server Error' });
    expect((error as { status: number }).status).toBe(500);
    expect(authService.logout).not.toHaveBeenCalled();
  });

  it('should logout when refresh fails', () => {
    const refreshError = new Error('refresh failed');
    authService.refreshSession.and.callFake(() => {
      authService.logout();
      return throwError(() => refreshError);
    });
    let error: unknown;
    http.get('/api/v1/admin/data').subscribe({ error: err => error = err });
    httpTestingController.expectOne('/api/v1/admin/data')
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(error).toBe(refreshError);
    expect(authService.logout).toHaveBeenCalled();
  });

  it('should share one refresh across concurrent 401 responses', () => {
    authService.refreshSession.and.returnValue(of('new-token'));
    let results = 0;
    http.get('/api/v1/admin/one').subscribe(() => results++);
    http.get('/api/v1/admin/two').subscribe(() => results++);
    httpTestingController.expectOne('/api/v1/admin/one')
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    httpTestingController.expectOne('/api/v1/admin/two')
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    httpTestingController.expectOne('/api/v1/admin/one').flush({ one: true });
    httpTestingController.expectOne('/api/v1/admin/two').flush({ two: true });
    expect(results).toBe(2);
    expect(authService.refreshSession).toHaveBeenCalledTimes(2);
  });

  it('should not refresh login or refresh endpoint requests', () => {
    for (const url of ['/api/v1/auth/login', '/api/v1/auth/refresh']) {
      http.post(url, {}).subscribe({ error: () => undefined });
      const request = httpTestingController.expectOne(url);
      expect(request.request.headers.has('Authorization')).toBeFalse();
      request.flush({}, { status: 401, statusText: 'Unauthorized' });
    }
    expect(authService.refreshSession).not.toHaveBeenCalled();
    expect(authService.logout).not.toHaveBeenCalled();
  });

  it('should not refresh or logout requests with explicit credentials', () => {
    http.get('/api/v1/auth/profile', {
      headers: { Authorization: 'Bearer caller-managed-token' },
    }).subscribe({ error: () => undefined });
    const request = httpTestingController.expectOne('/api/v1/auth/profile');
    expect(request.request.headers.get('Authorization')).toBe('Bearer caller-managed-token');
    request.flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(authService.refreshSession).not.toHaveBeenCalled();
    expect(authService.logout).not.toHaveBeenCalled();
  });
});
