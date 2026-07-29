import { Component, OnInit, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatTableModule, MatTableDataSource } from '@angular/material/table';
import { MatPaginatorModule, MatPaginator } from '@angular/material/paginator';
import { MatSortModule, MatSort } from '@angular/material/sort';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatButtonModule } from '@angular/material/button';
import { AdminApiService } from '../../core/services/admin-api.service';
import { AuditEvent } from '../../core/models/audit.model';

@Component({
    selector: 'app-audit',
    imports: [
        CommonModule, FormsModule, MatTableModule, MatPaginatorModule, MatSortModule,
        MatInputModule, MatFormFieldModule, MatSelectModule, MatIconModule, MatChipsModule,
        MatProgressSpinnerModule, MatButtonModule,
    ],
    template: `
    <div class="page-container">
      <h1 class="page-title">Audit Logs</h1>

      <div class="toolbar">
        <mat-form-field appearance="outline" class="search-field">
          <mat-label>Search audit events</mat-label>
          <input matInput (keyup)="applyFilter($event)" placeholder="Search by user, action, or resource">
          <mat-icon matSuffix>search</mat-icon>
        </mat-form-field>

        <mat-form-field appearance="outline" class="filter-field">
          <mat-label>Action</mat-label>
          <mat-select [(value)]="actionFilter" (selectionChange)="applyFilters()">
            <mat-option value="">All Actions</mat-option>
            <mat-option value="create">Create</mat-option>
            <mat-option value="update">Update</mat-option>
            <mat-option value="delete">Delete</mat-option>
            <mat-option value="share">Share</mat-option>
            <mat-option value="login">Login</mat-option>
            <mat-option value="upload">Upload</mat-option>
            <mat-option value="download">Download</mat-option>
            <mat-option value="suspend">Suspend</mat-option>
          </mat-select>
        </mat-form-field>

        <mat-form-field appearance="outline" class="filter-field">
          <mat-label>Severity</mat-label>
          <mat-select [(value)]="severityFilter" (selectionChange)="applyFilters()">
            <mat-option value="">All Severities</mat-option>
            <mat-option value="info">Info</mat-option>
            <mat-option value="warning">Warning</mat-option>
            <mat-option value="critical">Critical</mat-option>
          </mat-select>
        </mat-form-field>
      </div>

      <div *ngIf="loading" class="loading-container">
        <mat-spinner diameter="40"></mat-spinner>
      </div>

      <div class="table-container" *ngIf="!loading">
        <table mat-table [dataSource]="dataSource" matSort class="audit-table">
          <ng-container matColumnDef="timestamp">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Timestamp</th>
            <td mat-cell *matCellDef="let event">{{ event.timestamp | date:'medium' }}</td>
          </ng-container>

          <ng-container matColumnDef="userName">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>User</th>
            <td mat-cell *matCellDef="let event">{{ event.userName }}</td>
          </ng-container>

          <ng-container matColumnDef="action">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Action</th>
            <td mat-cell *matCellDef="let event">
              <span class="action-chip" [class]="'action-' + event.action">{{ event.action }}</span>
            </td>
          </ng-container>

          <ng-container matColumnDef="resourceName">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Resource</th>
            <td mat-cell *matCellDef="let event">
              <div class="resource-cell">
                <span class="resource-type">{{ event.resourceType }}</span>
                <span>{{ event.resourceName }}</span>
              </div>
            </td>
          </ng-container>

          <ng-container matColumnDef="severity">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Severity</th>
            <td mat-cell *matCellDef="let event">
              <span class="severity-indicator" [class]="'severity-' + event.severity">
                <span class="severity-dot"></span>
                {{ event.severity }}
              </span>
            </td>
          </ng-container>

          <ng-container matColumnDef="details">
            <th mat-header-cell *matHeaderCellDef>Details</th>
            <td mat-cell *matCellDef="let event">{{ event.details }}</td>
          </ng-container>

          <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
          <tr mat-row *matRowDef="let row; columns: displayedColumns;"></tr>
        </table>

        <mat-paginator [pageSizeOptions]="[10, 25, 50]" showFirstLastButtons></mat-paginator>
      </div>
    </div>
  `,
    styles: [`
    .page-container { padding: 0; }
    .page-title { font-size: 1.5rem; font-weight: 600; color: #333; margin-bottom: 24px; }
    .loading-container { display: flex; justify-content: center; padding: 60px; }

    .toolbar { display: flex; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
    .search-field { flex: 1; min-width: 250px; }
    .filter-field { width: 160px; }

    .table-container {
      background: white; border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.08); overflow: hidden;
    }

    .audit-table { width: 100%; }

    .action-chip {
      padding: 3px 8px; border-radius: 4px; font-size: 0.75rem;
      font-weight: 600; text-transform: uppercase;
    }

    .action-create { background: #e8f5e9; color: #2e7d32; }
    .action-update { background: #e3f2fd; color: #1565c0; }
    .action-delete { background: #ffebee; color: #c62828; }
    .action-share { background: #f3e5f5; color: #7b1fa2; }
    .action-login { background: #fff3e0; color: #e65100; }
    .action-logout { background: #eceff1; color: #546e7a; }
    .action-upload { background: #e0f7fa; color: #00695c; }
    .action-download { background: #fce4ec; color: #ad1457; }
    .action-suspend { background: #ffebee; color: #b71c1c; }
    .action-restore { background: #e8f5e9; color: #1b5e20; }

    .resource-cell { display: flex; flex-direction: column; }
    .resource-type { font-size: 0.7rem; color: #999; text-transform: uppercase; }

    .severity-indicator {
      display: flex; align-items: center; gap: 6px;
      font-size: 0.8rem; font-weight: 500; text-transform: capitalize;
    }

    .severity-dot {
      width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    }

    .severity-info .severity-dot { background: #4caf50; }
    .severity-info { color: #4caf50; }
    .severity-warning .severity-dot { background: #ff9800; }
    .severity-warning { color: #ff9800; }
    .severity-critical .severity-dot { background: #f44336; }
    .severity-critical { color: #f44336; }
  `]
})
export class AuditComponent implements OnInit {
  displayedColumns = ['timestamp', 'userName', 'action', 'resourceName', 'severity', 'details'];
  dataSource = new MatTableDataSource<AuditEvent>([]);
  loading = true;
  actionFilter = '';
  severityFilter = '';
  private searchFilter = '';

  @ViewChild(MatPaginator) set matPaginator(paginator: MatPaginator) {
    this.dataSource.paginator = paginator;
  }
  @ViewChild(MatSort) set matSort(sort: MatSort) {
    this.dataSource.sort = sort;
  }

  constructor(private api: AdminApiService) {}

  ngOnInit(): void {
    this.api.getAuditEvents().subscribe(events => {
      this.dataSource.data = events;
      this.loading = false;
      this.setupFilterPredicate();
    });
  }

  applyFilter(event: Event): void {
    this.searchFilter = (event.target as HTMLInputElement).value.trim().toLowerCase();
    this.dataSource.filter = this.searchFilter || ' ';
  }

  applyFilters(): void {
    this.dataSource.filter = this.searchFilter || ' ';
  }

  private setupFilterPredicate(): void {
    this.dataSource.filterPredicate = (data: AuditEvent) => {
      const matchesSearch = !this.searchFilter ||
        data.userName.toLowerCase().includes(this.searchFilter) ||
        data.action.toLowerCase().includes(this.searchFilter) ||
        data.resourceName.toLowerCase().includes(this.searchFilter) ||
        data.details.toLowerCase().includes(this.searchFilter);
      const matchesAction = !this.actionFilter || data.action === this.actionFilter;
      const matchesSeverity = !this.severityFilter || data.severity === this.severityFilter;
      return matchesSearch && matchesAction && matchesSeverity;
    };
  }
}
