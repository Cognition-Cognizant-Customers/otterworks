import { Injectable } from '@angular/core';

/**
 * Holds the local-development bearer token used by the mocked admin login so
 * that requests are accepted by admin-service on a developer machine. The value
 * is loaded at bootstrap from the static asset `assets/dev-auth.json`, which is
 * generated from `JWT_SECRET` by `scripts/generate-dev-auth.mjs` and can be
 * replaced per environment without rebuilding the bundle. When the asset is
 * absent or carries no token, the dashboard falls back to a placeholder token.
 */
@Injectable({ providedIn: 'root' })
export class DevAuthConfigService {
  private devToken: string | null = null;

  get token(): string | null {
    return this.devToken;
  }

  async load(): Promise<void> {
    try {
      const response = await fetch('assets/dev-auth.json', { cache: 'no-store' });
      if (!response.ok) {
        return;
      }
      const config = (await response.json()) as { token?: string };
      this.devToken = config.token?.trim() ? config.token : null;
    } catch {
      this.devToken = null;
    }
  }
}
