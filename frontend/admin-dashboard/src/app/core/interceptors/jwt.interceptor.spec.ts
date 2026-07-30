import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { HTTP_INTERCEPTORS, HttpClient, provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import { RouterTestingModule } from '@angular/router/testing';
import { JwtInterceptor } from './jwt.interceptor';
import { AuthService } from '../services/auth.service';

describe('JwtInterceptor', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  let authService: AuthService;

  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({
      imports: [RouterTestingModule],
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
        { provide: HTTP_INTERCEPTORS, useClass: JwtInterceptor, multi: true },
      ],
    });
    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
    authService = TestBed.inject(AuthService);
  });

  afterEach(() => {
    httpMock.verify();
    localStorage.clear();
  });

  it('should not add Authorization header when no token is stored', () => {
    http.get('/api/v1/admin/users').subscribe();

    const req = httpMock.expectOne('/api/v1/admin/users');
    expect(req.request.headers.has('Authorization')).toBeFalse();
    req.flush({ users: [] });
  });

  it('should add Bearer Authorization header when a token is stored', () => {
    localStorage.setItem('ow_admin_token', 'test-token');

    http.get('/api/v1/admin/users').subscribe();

    const req = httpMock.expectOne('/api/v1/admin/users');
    expect(req.request.headers.get('Authorization')).toBe('Bearer test-token');
    req.flush({ users: [] });
  });

  it('should log out on a 401 response', () => {
    localStorage.setItem('ow_admin_token', 'expired-token');
    spyOn(authService, 'logout');

    http.get('/api/v1/admin/users').subscribe({ error: () => undefined });

    const req = httpMock.expectOne('/api/v1/admin/users');
    req.flush({ error: 'unauthorized' }, { status: 401, statusText: 'Unauthorized' });

    expect(authService.logout).toHaveBeenCalled();
  });

  it('should propagate non-401 errors without logging out', () => {
    localStorage.setItem('ow_admin_token', 'valid-token');
    spyOn(authService, 'logout');
    let status = 0;

    http.get('/api/v1/admin/users').subscribe({ error: err => (status = err.status) });

    const req = httpMock.expectOne('/api/v1/admin/users');
    req.flush({ error: 'boom' }, { status: 500, statusText: 'Server Error' });

    expect(status).toBe(500);
    expect(authService.logout).not.toHaveBeenCalled();
  });
});
