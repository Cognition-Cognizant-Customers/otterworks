import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError, map, retry, tap } from 'rxjs/operators';

interface DevAuthConfig {
  token: string;
}

/**
 * Loads the local-dev bearer token used by the mocked admin login. The asset is generated
 * (and git-ignored) by `npm run gen:dev-auth`, which runs before `npm start` / `npm run build`.
 */
@Injectable({ providedIn: 'root' })
export class DevAuthConfigService {
  private config: DevAuthConfig | null = null;

  constructor(private http: HttpClient) {}

  get token(): string | null {
    return this.config?.token || null;
  }

  load(): Observable<void> {
    return this.http.get<DevAuthConfig>('/assets/dev-auth.json').pipe(
      retry(1),
      tap(config => (this.config = config)),
      map(() => undefined),
      catchError(() => {
        console.warn('dev-auth.json could not be loaded; the mocked login will issue a token the backend rejects');
        return of(undefined);
      })
    );
  }
}
