import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, of, throwError } from 'rxjs';
import { tap, delay, map } from 'rxjs/operators';
import { Router } from '@angular/router';
import { DevAuthConfigService } from './dev-auth-config.service';

export interface AuthUser {
  id: string;
  email: string;
  displayName: string;
  role: string;
  token: string;
}

interface LoginResponse {
  user: AuthUser;
  token: string;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly TOKEN_KEY = 'ow_admin_token';
  private readonly USER_KEY = 'ow_admin_user';
  private currentUserSubject = new BehaviorSubject<AuthUser | null>(this.getStoredUser());
  currentUser$ = this.currentUserSubject.asObservable();

  constructor(
    private http: HttpClient,
    private router: Router,
    private devAuthConfig: DevAuthConfigService,
  ) {}

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
    // In production, this would call the real API:
    // return this.http.post<LoginResponse>('/api/v1/admin/auth/login', { email, password })
    return this.mockLogin(email, password).pipe(
      tap(user => {
        localStorage.setItem(this.TOKEN_KEY, user.token);
        localStorage.setItem(this.USER_KEY, JSON.stringify(user));
        this.currentUserSubject.next(user);
      })
    );
  }

  logout(): void {
    localStorage.removeItem(this.TOKEN_KEY);
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

  private mockLogin(email: string, password: string): Observable<AuthUser> {
    if (password.length < 1) {
      return throwError(() => new Error('Invalid credentials'));
    }
    const id = 'a0000000-0000-0000-0000-000000000001';
    const user: AuthUser = {
      id,
      email,
      displayName: 'Admin User',
      role: 'admin',
      token: this.devAuthConfig.token ?? `mock-jwt-token-${id}`,
    };
    return of(user).pipe(delay(800));
  }
}
