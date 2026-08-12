import { Component, OnInit, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatTableModule, MatTableDataSource } from '@angular/material/table';
import { MatPaginatorModule, MatPaginator } from '@angular/material/paginator';
import { MatSortModule, MatSort } from '@angular/material/sort';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { AdminApiService } from '../../core/services/admin-api.service';
import { User } from '../../core/models/user.model';

@Component({
    selector: 'app-quotas',
    imports: [
        CommonModule, FormsModule, MatTableModule, MatPaginatorModule, MatSortModule,
        MatInputModule, MatFormFieldModule, MatButtonModule, MatIconModule,
        MatProgressSpinnerModule, MatProgressBarModule, MatSelectModule, MatSnackBarModule,
    ],
    template: `
    <div class="page-container">
      <h1 class="page-title">Storage Quotas</h1>
    
      <div class="toolbar">
        <mat-form-field appearance="outline" class="search-field">
          <mat-label>Search users</mat-label>
          <input matInput (keyup)="applyFilter($event)" placeholder="Search by name or email">
          <mat-icon matSuffix>search</mat-icon>
        </mat-form-field>
      </div>
    
      @if (loading) {
        <div class="loading-container">
          <mat-spinner diameter="40"></mat-spinner>
        </div>
      }
    
      @if (!loading) {
        <div class="table-container">
          <table mat-table [dataSource]="dataSource" matSort class="quotas-table">
            <ng-container matColumnDef="displayName">
              <th mat-header-cell *matHeaderCellDef mat-sort-header>User</th>
              <td mat-cell *matCellDef="let user">
                <div class="user-cell">
                  <mat-icon class="user-avatar">account_circle</mat-icon>
                  <div>
                    <div class="user-name">{{ user.displayName }}</div>
                    <div class="user-email">{{ user.email }}</div>
                  </div>
                </div>
              </td>
            </ng-container>
            <ng-container matColumnDef="storageUsed">
              <th mat-header-cell *matHeaderCellDef mat-sort-header>Used</th>
              <td mat-cell *matCellDef="let user">{{ formatBytes(user.storageUsed) }}</td>
            </ng-container>
            <ng-container matColumnDef="storageQuota">
              <th mat-header-cell *matHeaderCellDef mat-sort-header>Quota</th>
              <td mat-cell *matCellDef="let user">{{ formatBytes(user.storageQuota) }}</td>
            </ng-container>
            <ng-container matColumnDef="usage">
              <th mat-header-cell *matHeaderCellDef>Usage</th>
              <td mat-cell *matCellDef="let user">
                <div class="usage-cell">
                  <mat-progress-bar
                    mode="determinate"
                    [value]="getUsagePercent(user)"
                    [color]="getUsagePercent(user) > 90 ? 'warn' : 'primary'">
                  </mat-progress-bar>
                  <span class="usage-label">{{ getUsagePercent(user) | number:'1.0-0' }}%</span>
                </div>
              </td>
            </ng-container>
            <ng-container matColumnDef="actions">
              <th mat-header-cell *matHeaderCellDef>Update Quota</th>
              <td mat-cell *matCellDef="let user">
                <div class="quota-actions">
                  <mat-form-field appearance="outline" class="quota-select">
                    <mat-select [value]="user.storageQuota" (selectionChange)="updateQuota(user, $event.value)">
                      <mat-option [value]="1 * gb">1 GB</mat-option>
                      <mat-option [value]="2 * gb">2 GB</mat-option>
                      <mat-option [value]="5 * gb">5 GB</mat-option>
                      <mat-option [value]="10 * gb">10 GB</mat-option>
                      <mat-option [value]="20 * gb">20 GB</mat-option>
                      <mat-option [value]="50 * gb">50 GB</mat-option>
                    </mat-select>
                  </mat-form-field>
                </div>
              </td>
            </ng-container>
            <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
            <tr mat-row *matRowDef="let row; columns: displayedColumns;"></tr>
          </table>
          <mat-paginator [pageSizeOptions]="[5, 10, 25]" showFirstLastButtons></mat-paginator>
        </div>
      }
    </div>
    `,
    styles: [`
    .page-container { padding: 0; }
    .page-title { font-size: 1.5rem; font-weight: 600; color: #333; margin-bottom: 24px; }
    .loading-container { display: flex; justify-content: center; padding: 60px; }

    .toolbar { display: flex; gap: 16px; margin-bottom: 16px; }
    .search-field { flex: 1; max-width: 400px; }

    .table-container {
      background: white; border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.08); overflow: hidden;
    }

    .quotas-table { width: 100%; }

    .user-cell { display: flex; align-items: center; gap: 12px; }
    .user-avatar { color: #bdbdbd; font-size: 32px; width: 32px; height: 32px; }
    .user-name { font-weight: 500; }
    .user-email { font-size: 0.8rem; color: #999; }

    .usage-cell { display: flex; align-items: center; gap: 12px; min-width: 150px; }
    .usage-label { font-size: 0.85rem; font-weight: 500; min-width: 40px; }

    .quota-actions { display: flex; align-items: center; }
    .quota-select { width: 120px; }

    :host ::ng-deep .quota-select .mat-mdc-form-field-subscript-wrapper { display: none; }
  `]
})
export class QuotasComponent implements OnInit {
  displayedColumns = ['displayName', 'storageUsed', 'storageQuota', 'usage', 'actions'];
  dataSource = new MatTableDataSource<User>([]);
  loading = true;
  gb = 1024 * 1024 * 1024;

  @ViewChild(MatPaginator) set matPaginator(paginator: MatPaginator) {
    this.dataSource.paginator = paginator;
  }
  @ViewChild(MatSort) set matSort(sort: MatSort) {
    this.dataSource.sort = sort;
  }

  constructor(private api: AdminApiService, private snackBar: MatSnackBar) {}

  ngOnInit(): void {
    this.api.getUsers().subscribe(users => {
      this.dataSource.data = users;
      this.loading = false;
    });
  }

  applyFilter(event: Event): void {
    const filterValue = (event.target as HTMLInputElement).value;
    this.dataSource.filter = filterValue.trim().toLowerCase();
  }

  formatBytes(bytes: number): string {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  getUsagePercent(user: User): number {
    return user.storageQuota > 0 ? (user.storageUsed / user.storageQuota) * 100 : 0;
  }

  updateQuota(user: User, newQuota: number): void {
    this.api.updateStorageQuota(user.id, newQuota).subscribe(() => {
      this.snackBar.open(`Quota updated for ${user.displayName}`, 'Dismiss', { duration: 3000 });
    });
  }
}
