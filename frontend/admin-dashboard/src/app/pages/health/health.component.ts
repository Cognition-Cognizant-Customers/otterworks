import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { catchError, of } from 'rxjs';
import { AdminApiService } from '../../core/services/admin-api.service';
import { ServiceHealth } from '../../core/models/system-health.model';

@Component({
    selector: 'app-health',
    imports: [
        CommonModule, MatCardModule, MatIconModule, MatButtonModule,
        MatProgressSpinnerModule, MatChipsModule,
    ],
    template: `
    <div class="page-container">
      <div class="page-header">
        <h1 class="page-title">System Health</h1>
        <button mat-raised-button color="primary" (click)="refresh()">
          <mat-icon>refresh</mat-icon> Refresh
        </button>
      </div>
    
      @if (!loading) {
        <div class="health-summary">
          <div class="summary-card healthy">
            <mat-icon>check_circle</mat-icon>
            <span class="summary-count">{{ healthyCounts.healthy }}</span>
            <span class="summary-label">Healthy</span>
          </div>
          <div class="summary-card degraded">
            <mat-icon>warning</mat-icon>
            <span class="summary-count">{{ healthyCounts.degraded }}</span>
            <span class="summary-label">Degraded</span>
          </div>
          <div class="summary-card down">
            <mat-icon>error</mat-icon>
            <span class="summary-count">{{ healthyCounts.down }}</span>
            <span class="summary-label">Down</span>
          </div>
        </div>
      }
    
      @if (loading) {
        <div class="loading-container">
          <mat-spinner diameter="40"></mat-spinner>
        </div>
      }
    
      @if (error) {
        <div class="error-container">
          <mat-icon>error_outline</mat-icon>
          <p>Failed to load health data. The health endpoint may be unavailable.</p>
          <button mat-raised-button color="primary" (click)="refresh()">Retry</button>
        </div>
      }
    
      @if (!loading) {
        <div class="services-grid">
          @for (service of services; track service) {
            <mat-card class="service-card" [class]="'border-' + service.status">
              <mat-card-content>
                <div class="service-header">
                  <div class="service-name-row">
                    <span class="status-dot" [class]="'dot-' + service.status"></span>
                    <h3>{{ service.name }}</h3>
                  </div>
                  <span class="service-version">v{{ service.version }}</span>
                </div>
                <p class="service-details">{{ service.details }}</p>
                <div class="service-meta">
                  <div class="meta-item">
                    <mat-icon>code</mat-icon>
                    <span>{{ service.language }}</span>
                  </div>
                  <div class="meta-item">
                    <mat-icon>lan</mat-icon>
                    <span>Port {{ service.port }}</span>
                  </div>
                  <div class="meta-item">
                    <mat-icon>speed</mat-icon>
                    <span>{{ service.responseTime > 0 ? service.responseTime + 'ms' : 'N/A' }}</span>
                  </div>
                  <div class="meta-item">
                    <mat-icon>schedule</mat-icon>
                    <span>{{ service.uptime }} uptime</span>
                  </div>
                </div>
                <div class="service-status">
                  <span class="status-badge" [class]="'badge-' + service.status">
                    {{ service.status | uppercase }}
                  </span>
                  <span class="last-checked">Checked {{ service.lastChecked | date:'shortTime' }}</span>
                </div>
              </mat-card-content>
            </mat-card>
          }
        </div>
      }
    </div>
    `,
    styles: [`
    .page-container { padding: 0; }
    .page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
    .page-title { font-size: 1.5rem; font-weight: 600; color: #333; margin: 0; }
    .loading-container { display: flex; justify-content: center; padding: 60px; }
    .error-container { display: flex; flex-direction: column; align-items: center; padding: 60px; color: #c62828; }
    .error-container .mat-icon { font-size: 48px; width: 48px; height: 48px; margin-bottom: 16px; }
    .error-container p { margin-bottom: 16px; }

    .health-summary {
      display: flex; gap: 16px; margin-bottom: 24px;
    }

    .summary-card {
      display: flex; align-items: center; gap: 12px;
      padding: 16px 24px; border-radius: 8px; flex: 1;
    }

    .summary-card.healthy { background: #e8f5e9; color: #2e7d32; }
    .summary-card.degraded { background: #fff3e0; color: #e65100; }
    .summary-card.down { background: #ffebee; color: #c62828; }

    .summary-count { font-size: 2rem; font-weight: 700; }
    .summary-label { font-size: 0.9rem; font-weight: 500; }

    .services-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
      gap: 16px;
    }

    .service-card { border-left: 4px solid transparent; }
    .border-healthy { border-left-color: #4caf50; }
    .border-degraded { border-left-color: #ff9800; }
    .border-down { border-left-color: #f44336; }

    .service-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
    .service-name-row { display: flex; align-items: center; gap: 8px; }
    .service-name-row h3 { margin: 0; font-size: 1rem; }
    .service-version { font-size: 0.8rem; color: #999; font-family: monospace; }

    .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .dot-healthy { background: #4caf50; }
    .dot-degraded { background: #ff9800; }
    .dot-down { background: #f44336; }

    .service-details { color: #666; font-size: 0.85rem; line-height: 1.4; margin: 0 0 16px; }

    .service-meta {
      display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 16px;
    }

    .meta-item { display: flex; align-items: center; gap: 6px; font-size: 0.8rem; color: #666; }
    .meta-item .mat-icon { font-size: 16px; width: 16px; height: 16px; color: #999; }

    .service-status { display: flex; justify-content: space-between; align-items: center; }

    .status-badge {
      padding: 4px 10px; border-radius: 4px; font-size: 0.7rem;
      font-weight: 700; letter-spacing: 0.5px;
    }

    .badge-healthy { background: #e8f5e9; color: #2e7d32; }
    .badge-degraded { background: #fff3e0; color: #e65100; }
    .badge-down { background: #ffebee; color: #c62828; }

    .last-checked { font-size: 0.75rem; color: #999; }
  `]
})
export class HealthComponent implements OnInit {
  services: ServiceHealth[] = [];
  loading = true;
  error = false;
  healthyCounts = { healthy: 0, degraded: 0, down: 0 };

  constructor(private api: AdminApiService) {}

  ngOnInit(): void {
    this.loadHealth();
  }

  loadHealth(): void {
    this.loading = true;
    this.error = false;
    this.api.getSystemHealth().pipe(
      catchError(() => {
        this.error = true;
        this.loading = false;
        return of([]);
      }),
    ).subscribe(services => {
      this.services = services;
      this.healthyCounts = {
        healthy: services.filter(s => s.status === 'healthy').length,
        degraded: services.filter(s => s.status === 'degraded').length,
        down: services.filter(s => s.status === 'down').length,
      };
      this.loading = false;
    });
  }

  refresh(): void {
    this.loadHealth();
  }
}
