import { Injectable } from '@angular/core';
import {
  HttpInterceptor,
  HttpRequest,
  HttpHandler,
  HttpEvent,
  HttpErrorResponse,
  HttpContextToken,
} from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError, switchMap } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';

const RETRIED_REQUEST = new HttpContextToken<boolean>(() => false);

@Injectable()
export class JwtInterceptor implements HttpInterceptor {
  constructor(private authService: AuthService) {}

  intercept(request: HttpRequest<unknown>, next: HttpHandler): Observable<HttpEvent<unknown>> {
    const token = this.authService.getToken();
    const hasExplicitAuthorization = request.headers.has('Authorization');

    const isAuthEndpoint = request.url.endsWith('/api/v1/auth/login')
      || request.url.endsWith('/api/v1/auth/refresh');

    if (token && !hasExplicitAuthorization && !isAuthEndpoint) {
      request = request.clone({
        setHeaders: {
          Authorization: `Bearer ${token}`,
        },
      });
    }

    return next.handle(request).pipe(
      catchError((error: HttpErrorResponse) => {
        if (
          error.status !== 401
          || isAuthEndpoint
          || hasExplicitAuthorization
          || request.context.get(RETRIED_REQUEST)
        ) {
          return throwError(() => error);
        }

        if (!this.authService.getRefreshToken()) {
          this.authService.logout();
          return throwError(() => error);
        }

        return this.authService.refreshSession().pipe(
          catchError(refreshError => throwError(() => refreshError)),
          switchMap(newToken => next.handle(request.clone({
            setHeaders: { Authorization: `Bearer ${newToken}` },
            context: request.context.set(RETRIED_REQUEST, true),
          }))),
        );
      })
    );
  }
}
