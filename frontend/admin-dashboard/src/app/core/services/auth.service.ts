import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { BehaviorSubject, Observable, throwError } from 'rxjs';
import { catchError, finalize, map, shareReplay, switchMap, tap } from 'rxjs/operators';
import { Router } from '@angular/router';

export interface AuthUser {
  id: string;
  email: string;
  displayName: string;
  role: string;
  token: string;
}

interface AuthResponse {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  expiresIn: number;
  user: {
    id: string;
    email: string;
    displayName: string;
    avatarUrl: string | null;
  };
}

interface UserProfile {
  id: string;
  email: string;
  displayName: string;
  avatarUrl: string | null;
  roles: string[];
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly TOKEN_KEY = 'ow_admin_token';
  private readonly REFRESH_TOKEN_KEY = 'ow_admin_refresh_token';
  private readonly USER_KEY = 'ow_admin_user';
  private refreshInFlight$: Observable<string> | null = null;
  private currentUserSubject = new BehaviorSubject<AuthUser | null>(this.getStoredUser());
  currentUser$ = this.currentUserSubject.asObservable();

  constructor(private http: HttpClient, private router: Router) {}

  get isAuthenticated(): boolean {
    return !!this.getToken();
  }

  get currentUser(): AuthUser | null {
    return this.currentUserSubject.value;
  }

  getToken(): string | null {
    return localStorage.getItem(this.TOKEN_KEY);
  }

  login(email: string, password: string): Observable<AuthUser> {
    if (password.length < 1) {
      return throwError(() => new Error('Invalid credentials'));
    }

    return this.http.post<AuthResponse>('/api/v1/auth/login', { email, password }).pipe(
      catchError(error => {
        if (error instanceof HttpErrorResponse && [400, 401].includes(error.status)) {
          return throwError(() => new Error('Invalid credentials'));
        }
        return throwError(() => error instanceof Error ? error : new Error('Login failed'));
      }),
      switchMap(response => this.http.get<UserProfile>('/api/v1/auth/profile', {
        headers: new HttpHeaders({ Authorization: `Bearer ${response.accessToken}` }),
      }).pipe(
        map(profile => {
          if (!profile.roles.includes('ADMIN')) {
            throw new Error('Insufficient privileges: admin access required');
          }
          return { response, profile };
        }),
        catchError(error => throwError(() => error instanceof Error
          ? error
          : new Error('Unable to verify admin privileges'))),
      )),
      map(({ response, profile }) => ({
        user: this.toAuthUser(profile, response.accessToken),
        refreshToken: response.refreshToken,
      })),
      tap(({ user, refreshToken }) => this.persistSession(user, refreshToken)),
      map(({ user }) => user),
    );
  }

  getRefreshToken(): string | null {
    return localStorage.getItem(this.REFRESH_TOKEN_KEY);
  }

  refreshSession(): Observable<string> {
    if (this.refreshInFlight$) {
      return this.refreshInFlight$;
    }

    const refreshToken = this.getRefreshToken();
    if (!refreshToken) {
      this.logout();
      return throwError(() => new Error('No refresh token available'));
    }

    this.refreshInFlight$ = this.http.post<AuthResponse>(
      '/api/v1/auth/refresh',
      {},
      { headers: new HttpHeaders({ Authorization: `Bearer ${refreshToken}` }) },
    ).pipe(
      switchMap(response => this.http.get<UserProfile>('/api/v1/auth/profile', {
        headers: new HttpHeaders({ Authorization: `Bearer ${response.accessToken}` }),
      }).pipe(
        map(profile => {
          if (!profile.roles.includes('ADMIN')) {
            throw new Error('Insufficient privileges: admin access required');
          }
          return {
            user: this.toAuthUser(profile, response.accessToken),
            refreshToken: response.refreshToken,
          };
        }),
        catchError(error => throwError(() => error instanceof Error
          ? error
          : new Error('Unable to verify admin privileges'))),
      )),
      tap(({ user, refreshToken }) => this.persistSession(user, refreshToken)),
      map(({ user }) => user.token),
      catchError(error => {
        this.logout();
        return throwError(() => error instanceof Error ? error : new Error('Session refresh failed'));
      }),
      finalize(() => {
        this.refreshInFlight$ = null;
      }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );

    return this.refreshInFlight$;
  }

  logout(): void {
    localStorage.removeItem(this.TOKEN_KEY);
    localStorage.removeItem(this.REFRESH_TOKEN_KEY);
    localStorage.removeItem(this.USER_KEY);
    this.currentUserSubject.next(null);
    this.router.navigate(['/login']);
  }

  private getStoredUser(): AuthUser | null {
    const stored = localStorage.getItem(this.USER_KEY);
    if (stored) {
      try {
        return JSON.parse(stored) as AuthUser;
      } catch {
        return null;
      }
    }
    return null;
  }

  private toAuthUser(user: UserProfile, token: string): AuthUser {
    return {
      id: user.id,
      email: user.email,
      displayName: user.displayName,
      role: user.roles.includes('ADMIN') ? 'admin' : 'user',
      token,
    };
  }

  private persistSession(user: AuthUser, refreshToken: string): void {
    localStorage.setItem(this.TOKEN_KEY, user.token);
    localStorage.setItem(this.REFRESH_TOKEN_KEY, refreshToken);
    localStorage.setItem(this.USER_KEY, JSON.stringify(user));
    this.currentUserSubject.next(user);
  }
}
