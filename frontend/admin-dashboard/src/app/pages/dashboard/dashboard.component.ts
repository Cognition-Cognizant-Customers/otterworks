import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { BaseChartDirective } from 'ng2-charts';
import { ChartConfiguration } from 'chart.js';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AdminApiService } from '../../core/services/admin-api.service';
import { DashboardStats } from '../../core/models/analytics.model';

@Component({
    selector: 'app-dashboard',
    imports: [CommonModule, MatCardModule, MatIconModule, MatProgressSpinnerModule, BaseChartDirective],
    template: `
    <div class="page-container">
      <h1 class="page-title">Dashboard</h1>

      <div *ngIf="loading" class="loading-container">
        <mat-spinner diameter="40"></mat-spinner>
      </div>

      <div *ngIf="!loading && stats">
        <div class="stats-grid">
          <mat-card class="stat-card">
            <mat-icon class="stat-icon users-icon">people</mat-icon>
            <div class="stat-info">
              <span class="stat-label">Total Users</span>
              <span class="stat-value">{{ stats.totalUsers | number }}</span>
              <span class="stat-growth" [class.positive]="stats.usersGrowth > 0" [class.negative]="stats.usersGrowth < 0">
                <mat-icon>{{ stats.usersGrowth > 0 ? 'trending_up' : 'trending_down' }}</mat-icon>
                {{ stats.usersGrowth }}%
              </span>
            </div>
          </mat-card>

          <mat-card class="stat-card">
            <mat-icon class="stat-icon docs-icon">description</mat-icon>
            <div class="stat-info">
              <span class="stat-label">Active Documents</span>
              <span class="stat-value">{{ stats.activeDocuments | number }}</span>
              <span class="stat-growth" [class.positive]="stats.documentsGrowth > 0" [class.negative]="stats.documentsGrowth < 0">
                <mat-icon>{{ stats.documentsGrowth > 0 ? 'trending_up' : 'trending_down' }}</mat-icon>
                {{ stats.documentsGrowth }}%
              </span>
            </div>
          </mat-card>

          <mat-card class="stat-card">
            <mat-icon class="stat-icon storage-icon">cloud</mat-icon>
            <div class="stat-info">
              <span class="stat-label">Storage Used</span>
              <span class="stat-value">{{ stats.storageUsed }}</span>
              <span class="stat-growth" [class.positive]="stats.storageGrowth > 0" [class.negative]="stats.storageGrowth < 0">
                <mat-icon>{{ stats.storageGrowth > 0 ? 'trending_up' : 'trending_down' }}</mat-icon>
                {{ stats.storageGrowth }}%
              </span>
            </div>
          </mat-card>

          <mat-card class="stat-card">
            <mat-icon class="stat-icon sessions-icon">devices</mat-icon>
            <div class="stat-info">
              <span class="stat-label">Active Sessions</span>
              <span class="stat-value">{{ stats.activeSessions | number }}</span>
              <span class="stat-growth" [class.positive]="stats.sessionsGrowth > 0" [class.negative]="stats.sessionsGrowth < 0">
                <mat-icon>{{ stats.sessionsGrowth > 0 ? 'trending_up' : 'trending_down' }}</mat-icon>
                {{ stats.sessionsGrowth }}%
              </span>
            </div>
          </mat-card>
        </div>

        <div class="charts-row">
          <mat-card class="chart-card">
            <mat-card-header>
              <mat-card-title>User Signups</mat-card-title>
            </mat-card-header>
            <mat-card-content>
              <canvas baseChart
                [datasets]="signupChartData.datasets"
                [labels]="signupChartData.labels"
                [options]="lineChartOptions"
                type="line">
              </canvas>
            </mat-card-content>
          </mat-card>

          <mat-card class="chart-card">
            <mat-card-header>
              <mat-card-title>Document Activity</mat-card-title>
            </mat-card-header>
            <mat-card-content>
              <canvas baseChart
                [datasets]="activityChartData.datasets"
                [labels]="activityChartData.labels"
                [options]="barChartOptions"
                type="bar">
              </canvas>
            </mat-card-content>
          </mat-card>
        </div>
      </div>
    </div>
  `,
    styles: [`
    .page-container { padding: 0; }
    .page-title { font-size: 1.5rem; font-weight: 600; color: #333; margin-bottom: 24px; }
    .loading-container { display: flex; justify-content: center; padding: 60px; }

    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 20px;
      margin-bottom: 24px;
    }

    .stat-card {
      display: flex;
      align-items: center;
      padding: 20px;
      gap: 16px;
    }

    .stat-icon {
      font-size: 40px;
      width: 40px;
      height: 40px;
      padding: 12px;
      border-radius: 12px;
      box-sizing: content-box;
    }

    .users-icon { background: #e3f2fd; color: #1976d2; }
    .docs-icon { background: #e8f5e9; color: #388e3c; }
    .storage-icon { background: #fff3e0; color: #f57c00; }
    .sessions-icon { background: #f3e5f5; color: #7b1fa2; }

    .stat-info { display: flex; flex-direction: column; }
    .stat-label { font-size: 0.8rem; color: #999; text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-value { font-size: 1.8rem; font-weight: 700; color: #333; }

    .stat-growth {
      display: flex;
      align-items: center;
      gap: 2px;
      font-size: 0.8rem;
      font-weight: 500;
    }

    .stat-growth .mat-icon { font-size: 16px; width: 16px; height: 16px; }
    .stat-growth.positive { color: #388e3c; }
    .stat-growth.negative { color: #d32f2f; }

    .charts-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
      gap: 20px;
    }

    .chart-card { padding: 16px; }
  `]
})
export class DashboardComponent implements OnInit, OnDestroy {
  stats: DashboardStats | null = null;
  loading = true;

  signupChartData: ChartConfiguration<'line'>['data'] = { labels: [], datasets: [] };
  activityChartData: ChartConfiguration<'bar'>['data'] = { labels: [], datasets: [] };

  lineChartOptions: ChartConfiguration<'line'>['options'] = {
    responsive: true,
    plugins: { legend: { display: false } },
    scales: { y: { beginAtZero: true } },
  };

  barChartOptions: ChartConfiguration<'bar'>['options'] = {
    responsive: true,
    plugins: { legend: { display: false } },
    scales: { y: { beginAtZero: true } },
  };

  private destroy$ = new Subject<void>();

  constructor(private api: AdminApiService) {}

  ngOnInit(): void {
    this.loadStats();

    this.api.statsChanged$
      .pipe(takeUntil(this.destroy$))
      .subscribe(() => this.loadStats());

    this.api.getAnalyticsReport().subscribe(report => {
      this.signupChartData = {
        labels: report.userSignups.map(d => d.label),
        datasets: [{
          data: report.userSignups.map(d => d.value),
          borderColor: '#1976d2',
          backgroundColor: 'rgba(25, 118, 210, 0.1)',
          fill: true,
          tension: 0.4,
        }],
      };

      this.activityChartData = {
        labels: report.documentActivity.map(d => d.label),
        datasets: [{
          data: report.documentActivity.map(d => d.value),
          backgroundColor: '#4fc3f7',
          borderRadius: 4,
        }],
      };
    });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  private loadStats(): void {
    this.api.getDashboardStats().subscribe((stats: DashboardStats) => {
      this.stats = stats;
      this.loading = false;
    });
  }
}
