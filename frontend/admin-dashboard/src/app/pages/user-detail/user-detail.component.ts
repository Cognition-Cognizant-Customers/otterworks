import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { MatChipsModule } from '@angular/material/chips';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { AdminApiService } from '../../core/services/admin-api.service';
import { User, UserActivity } from '../../core/models/user.model';
import { ConfirmDialogComponent } from '../../shared/components/confirm-dialog.component';

@Component({
    selector: 'app-user-detail',
    imports: [
        CommonModule, RouterModule, MatCardModule, MatIconModule, MatButtonModule,
        MatProgressSpinnerModule, MatProgressBarModule, MatTableModule, MatChipsModule,
        MatSnackBarModule, MatDialogModule,
    ],
    template: `
    <div class="page-container">
      <div class="page-header">
        <button mat-icon-button (click)="goBack()">
          <mat-icon>arrow_back</mat-icon>
        </button>
        <h1 class="page-title">User Details</h1>
      </div>
    
      @if (loading) {
        <div class="loading-container">
          <mat-spinner diameter="40"></mat-spinner>
        </div>
      }
    
      @if (!loading && user) {
        <div class="user-detail-grid">
          <mat-card class="profile-card">
            <mat-card-content>
              <div class="profile-header">
                <mat-icon class="profile-avatar">account_circle</mat-icon>
                <div class="profile-info">
                  <h2>{{ user.displayName }}</h2>
                  <p>{{ user.email }}</p>
                  <div class="profile-badges">
                    <span class="role-chip" [class]="'role-' + user.role">{{ user.role }}</span>
                    <span class="status-chip" [class]="'status-' + user.status">{{ user.status }}</span>
                  </div>
                </div>
              </div>
              <div class="profile-details">
                <div class="detail-row">
                  <mat-icon>business</mat-icon>
                  <span>{{ user.department || 'No department' }}</span>
                </div>
                <div class="detail-row">
                  <mat-icon>calendar_today</mat-icon>
                  <span>Joined {{ user.createdAt | date:'mediumDate' }}</span>
                </div>
                <div class="detail-row">
                  <mat-icon>login</mat-icon>
                  <span>Last login: {{ user.lastLogin ? (user.lastLogin | date:'medium') : 'Never' }}</span>
                </div>
                <div class="detail-row">
                  <mat-icon>description</mat-icon>
                  <span>{{ user.documentsCount }} documents</span>
                </div>
              </div>
              <div class="profile-actions">
                @if (user.status === 'active') {
                  <button mat-raised-button color="warn" (click)="suspendUser()">
                    <mat-icon>block</mat-icon> Suspend User
                  </button>
                }
                @if (user.status === 'suspended') {
                  <button mat-raised-button color="primary" (click)="restoreUser()">
                    <mat-icon>restore</mat-icon> Restore User
                  </button>
                }
                <button mat-raised-button color="warn" (click)="deleteUser()">
                  <mat-icon>delete</mat-icon> Delete User
                </button>
              </div>
            </mat-card-content>
          </mat-card>
          <mat-card class="storage-card">
            <mat-card-header>
              <mat-card-title>Storage Usage</mat-card-title>
            </mat-card-header>
            <mat-card-content>
              <div class="storage-info">
                <span class="storage-used">{{ formatBytes(user.storageUsed) }}</span>
                <span class="storage-separator">of</span>
                <span class="storage-quota">{{ formatBytes(user.storageQuota) }}</span>
              </div>
              <mat-progress-bar
                mode="determinate"
                [value]="storagePercentage"
                [color]="storagePercentage > 90 ? 'warn' : 'primary'">
              </mat-progress-bar>
              <span class="storage-percent">{{ storagePercentage | number:'1.0-0' }}% used</span>
            </mat-card-content>
          </mat-card>
          <mat-card class="activity-card">
            <mat-card-header>
              <mat-card-title>Recent Activity</mat-card-title>
            </mat-card-header>
            <mat-card-content>
              @if (activities.length > 0) {
                <table mat-table [dataSource]="activities" class="activity-table">
                  <ng-container matColumnDef="timestamp">
                    <th mat-header-cell *matHeaderCellDef>Time</th>
                    <td mat-cell *matCellDef="let act">{{ act.timestamp | date:'short' }}</td>
                  </ng-container>
                  <ng-container matColumnDef="action">
                    <th mat-header-cell *matHeaderCellDef>Action</th>
                    <td mat-cell *matCellDef="let act">{{ act.action }}</td>
                  </ng-container>
                  <ng-container matColumnDef="resource">
                    <th mat-header-cell *matHeaderCellDef>Resource</th>
                    <td mat-cell *matCellDef="let act">{{ act.resource }}</td>
                  </ng-container>
                  <ng-container matColumnDef="ipAddress">
                    <th mat-header-cell *matHeaderCellDef>IP Address</th>
                    <td mat-cell *matCellDef="let act">{{ act.ipAddress || '-' }}</td>
                  </ng-container>
                  <tr mat-header-row *matHeaderRowDef="activityColumns"></tr>
                  <tr mat-row *matRowDef="let row; columns: activityColumns;"></tr>
                </table>
              }
              @if (activities.length === 0) {
                <div class="empty-state">
                  <mat-icon>history</mat-icon>
                  <p>No recent activity</p>
                </div>
              }
            </mat-card-content>
          </mat-card>
        </div>
      }
    
      @if (!loading && !user) {
        <div class="not-found">
          <mat-icon>person_off</mat-icon>
          <h2>User not found</h2>
          <button mat-raised-button color="primary" routerLink="/users">Back to Users</button>
        </div>
      }
    </div>
    `,
    styles: [`
    .page-container { padding: 0; }
    .page-header { display: flex; align-items: center; gap: 8px; margin-bottom: 24px; }
    .page-title { font-size: 1.5rem; font-weight: 600; color: #333; margin: 0; }
    .loading-container { display: flex; justify-content: center; padding: 60px; }

    .user-detail-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }

    .profile-card { grid-column: 1; grid-row: 1 / 3; }
    .storage-card { grid-column: 2; grid-row: 1; }
    .activity-card { grid-column: 1 / -1; }

    .profile-header { display: flex; align-items: center; gap: 20px; margin-bottom: 24px; }
    .profile-avatar { font-size: 80px; width: 80px; height: 80px; color: #bdbdbd; }
    .profile-info h2 { margin: 0; font-size: 1.4rem; }
    .profile-info p { margin: 4px 0 8px; color: #666; }
    .profile-badges { display: flex; gap: 8px; }

    .role-chip, .status-chip {
      padding: 4px 10px; border-radius: 12px; font-size: 0.75rem;
      font-weight: 600; text-transform: uppercase;
    }

    .role-admin { background: #e3f2fd; color: #1565c0; }
    .role-editor { background: #e8f5e9; color: #2e7d32; }
    .role-viewer { background: #f3e5f5; color: #7b1fa2; }
    .status-active { background: #e8f5e9; color: #2e7d32; }
    .status-suspended { background: #ffebee; color: #c62828; }
    .status-pending { background: #fff3e0; color: #e65100; }

    .profile-details { margin-bottom: 24px; }
    .detail-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; color: #555; }
    .detail-row .mat-icon { color: #999; font-size: 20px; width: 20px; height: 20px; }

    .profile-actions { display: flex; gap: 12px; flex-wrap: wrap; }

    .storage-info { display: flex; align-items: baseline; gap: 6px; margin-bottom: 12px; }
    .storage-used { font-size: 1.5rem; font-weight: 700; color: #333; }
    .storage-separator { color: #999; }
    .storage-quota { font-size: 1rem; color: #666; }
    .storage-percent { font-size: 0.85rem; color: #999; margin-top: 8px; display: block; }

    .activity-table { width: 100%; }

    .empty-state {
      display: flex; flex-direction: column; align-items: center;
      padding: 40px; color: #999;
    }

    .empty-state .mat-icon { font-size: 48px; width: 48px; height: 48px; margin-bottom: 12px; }

    .not-found {
      display: flex; flex-direction: column; align-items: center;
      padding: 80px; color: #999;
    }

    .not-found .mat-icon { font-size: 64px; width: 64px; height: 64px; margin-bottom: 16px; }
    .not-found h2 { margin-bottom: 20px; }
  `]
})
export class UserDetailComponent implements OnInit {
  user: User | null = null;
  activities: UserActivity[] = [];
  loading = true;
  storagePercentage = 0;
  activityColumns = ['timestamp', 'action', 'resource', 'ipAddress'];

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private api: AdminApiService,
    private snackBar: MatSnackBar,
    private dialog: MatDialog,
  ) {}

  ngOnInit(): void {
    const userId = this.route.snapshot.paramMap.get('id');
    if (userId) {
      this.api.getUser(userId).subscribe(user => {
        if (user) {
          this.user = user;
          this.storagePercentage = user.storageQuota > 0 ? (user.storageUsed / user.storageQuota) * 100 : 0;
        }
        this.loading = false;
      });

      this.api.getUserActivity(userId).subscribe(activities => {
        this.activities = activities;
      });
    } else {
      this.loading = false;
    }
  }

  formatBytes(bytes: number): string {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  goBack(): void {
    this.router.navigate(['/users']);
  }

  suspendUser(): void {
    if (!this.user) return;
    const dialogRef = this.dialog.open(ConfirmDialogComponent, {
      data: { title: 'Suspend User', message: `Are you sure you want to suspend ${this.user.displayName}?`, confirmText: 'Suspend', confirmColor: 'warn' },
    });
    dialogRef.afterClosed().subscribe(result => {
      if (result && this.user) {
        this.api.suspendUser(this.user.id).subscribe(updated => {
          this.user = updated;
          this.snackBar.open('User suspended', 'Dismiss', { duration: 3000 });
        });
      }
    });
  }

  restoreUser(): void {
    if (!this.user) return;
    this.api.restoreUser(this.user.id).subscribe(updated => {
      this.user = updated;
      this.snackBar.open('User restored', 'Dismiss', { duration: 3000 });
    });
  }

  deleteUser(): void {
    if (!this.user) return;
    const dialogRef = this.dialog.open(ConfirmDialogComponent, {
      data: { title: 'Delete User', message: `Permanently delete ${this.user.displayName}? This cannot be undone.`, confirmText: 'Delete', confirmColor: 'warn' },
    });
    dialogRef.afterClosed().subscribe(result => {
      if (result && this.user) {
        this.api.deleteUser(this.user.id).subscribe(() => {
          this.snackBar.open('User deleted', 'Dismiss', { duration: 3000 });
          this.router.navigate(['/users']);
        });
      }
    });
  }
}
