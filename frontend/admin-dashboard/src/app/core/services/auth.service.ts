import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { BehaviorSubject, Observable, throwError } from 'rxjs';
import { catchError, map, tap } from 'rxjs/operators';
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

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly TOKEN_KEY = 'ow_admin_token';
  private readonly USER_KEY = 'ow_admin_user';
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
      map(response => {
        const roles = this.getRolesFromToken(response.accessToken);
        const role = roles.includes('ADMIN') ? 'admin' : 'user';
        if (role !== 'admin') {
          throw new Error('Insufficient privileges: admin access required');
        }
        return {
          id: response.user.id,
          email: response.user.email,
          displayName: response.user.displayName,
          role,
          token: response.accessToken,
        };
      }),
      catchError(error => {
        if (error instanceof HttpErrorResponse && [400, 401].includes(error.status)) {
          return throwError(() => new Error('Invalid credentials'));
        }
        return throwError(() => error instanceof Error ? error : new Error('Login failed'));
      }),
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

  private getRolesFromToken(token: string): string[] {
    const payload = token.split('.')[1];
    if (!payload) {
      return [];
    }

    try {
      const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
      const decoded = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')));
      return Array.isArray(decoded.roles) ? decoded.roles : [];
    } catch {
      return [];
    }
  }
}
