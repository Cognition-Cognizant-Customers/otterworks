import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatButtonModule } from '@angular/material/button';
import { BaseChartDirective } from 'ng2-charts';
import { ChartConfiguration } from 'chart.js';
import { AdminApiService } from '../../core/services/admin-api.service';
import { AnalyticsReport } from '../../core/models/analytics.model';

@Component({
  selector: 'app-analytics',
  standalone: true,
  imports: [
    CommonModule, MatCardModule, MatIconModule, MatProgressSpinnerModule,
    MatButtonModule, BaseChartDirective,
  ],
  template: `
    <div class="page-container">
      <h1 class="page-title">Analytics</h1>

      <div *ngIf="loading" class="loading-container">
        <mat-spinner diameter="40"></mat-spinner>
      </div>

      <div *ngIf="!loading" class="charts-grid">
        <mat-card class="chart-card span-2">
          <mat-card-header>
            <mat-card-title>Active Users Over Time</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <canvas baseChart
              [datasets]="activeUsersChart.datasets"
              [labels]="activeUsersChart.labels"
              [options]="lineChartOptions"
              type="line">
            </canvas>
          </mat-card-content>
        </mat-card>

        <mat-card class="chart-card">
          <mat-card-header>
            <mat-card-title>Storage Usage (TB)</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <canvas baseChart
              [datasets]="storageChart.datasets"
              [labels]="storageChart.labels"
              [options]="lineChartOptions"
              type="line">
            </canvas>
          </mat-card-content>
        </mat-card>

        <mat-card class="chart-card">
          <mat-card-header>
            <mat-card-title>Top File Types</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <canvas baseChart
              [datasets]="fileTypesChart.datasets"
              [labels]="fileTypesChart.labels"
              [options]="pieChartOptions"
              type="pie">
            </canvas>
          </mat-card-content>
        </mat-card>

        <mat-card class="chart-card span-2">
          <mat-card-header>
            <mat-card-title>User Signups (Monthly)</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <canvas baseChart
              [datasets]="signupsChart.datasets"
              [labels]="signupsChart.labels"
              [options]="barChartOptions"
              type="bar">
            </canvas>
          </mat-card-content>
        </mat-card>

        <mat-card class="chart-card span-2">
          <mat-card-header>
            <mat-card-title>Peak Usage Hours</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <canvas baseChart
              [datasets]="peakHoursChart.datasets"
              [labels]="peakHoursChart.labels"
              [options]="barChartOptions"
              type="bar">
            </canvas>
          </mat-card-content>
        </mat-card>
      </div>
    </div>
  `,
  styles: [`
    .page-container { padding: 0; }
    .page-title { font-size: 1.5rem; font-weight: 600; color: #333; margin-bottom: 24px; }
    .loading-container { display: flex; justify-content: center; padding: 60px; }

    .charts-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 20px;
    }

    .chart-card { padding: 16px; }
    .span-2 { grid-column: span 2; }

    @media (max-width: 960px) {
      .charts-grid { grid-template-columns: 1fr; }
      .span-2 { grid-column: span 1; }
    }
  `],
})
export class AnalyticsComponent implements OnInit {
  loading = true;

  activeUsersChart: ChartConfiguration<'line'>['data'] = { labels: [], datasets: [] };
  storageChart: ChartConfiguration<'line'>['data'] = { labels: [], datasets: [] };
  fileTypesChart: ChartConfiguration<'pie'>['data'] = { labels: [], datasets: [] };
  signupsChart: ChartConfiguration<'bar'>['data'] = { labels: [], datasets: [] };
  peakHoursChart: ChartConfiguration<'bar'>['data'] = { labels: [], datasets: [] };

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

  pieChartOptions: ChartConfiguration<'pie'>['options'] = {
    responsive: true,
    plugins: { legend: { position: 'right' } },
  };

  constructor(private api: AdminApiService) {}

  ngOnInit(): void {
    this.api.getAnalyticsReport().subscribe(report => {
      this.buildCharts(report);
      this.loading = false;
    });
  }

  private buildCharts(report: AnalyticsReport): void {
    this.activeUsersChart = {
      labels: report.activeUsers.map(d => d.label),
      datasets: [{
        data: report.activeUsers.map(d => d.value),
        borderColor: '#1976d2',
        backgroundColor: 'rgba(25, 118, 210, 0.1)',
        fill: true,
        tension: 0.4,
      }],
    };

    this.storageChart = {
      labels: report.storageUsage.map(d => d.label),
      datasets: [{
        data: report.storageUsage.map(d => d.value),
        borderColor: '#ff9800',
        backgroundColor: 'rgba(255, 152, 0, 0.1)',
        fill: true,
        tension: 0.4,
      }],
    };

    const pieColors = ['#1976d2', '#388e3c', '#f57c00', '#7b1fa2', '#616161'];
    this.fileTypesChart = {
      labels: report.topFileTypes.map(d => d.label),
      datasets: [{
        data: report.topFileTypes.map(d => d.value),
        backgroundColor: pieColors,
      }],
    };

    this.signupsChart = {
      labels: report.userSignups.map(d => d.label),
      datasets: [{
        data: report.userSignups.map(d => d.value),
        backgroundColor: '#4fc3f7',
        borderRadius: 4,
      }],
    };

    this.peakHoursChart = {
      labels: report.peakHours.map(d => d.label),
      datasets: [{
        data: report.peakHours.map(d => d.value),
        backgroundColor: '#7c4dff',
        borderRadius: 4,
      }],
    };
  }
}
