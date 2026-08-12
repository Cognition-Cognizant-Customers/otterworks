import { Component, OnInit, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatTableModule, MatTableDataSource } from '@angular/material/table';
import { MatPaginatorModule, MatPaginator } from '@angular/material/paginator';
import { MatSortModule, MatSort } from '@angular/material/sort';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatMenuModule } from '@angular/material/menu';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { AdminApiService } from '../../core/services/admin-api.service';
import { User } from '../../core/models/user.model';
import { ConfirmDialogComponent } from '../../shared/components/confirm-dialog.component';

@Component({
    selector: 'app-users',
    imports: [
        CommonModule, FormsModule, MatTableModule, MatPaginatorModule, MatSortModule,
        MatInputModule, MatFormFieldModule, MatButtonModule, MatIconModule, MatChipsModule,
        MatMenuModule, MatSelectModule, MatProgressSpinnerModule, MatSnackBarModule,
        MatDialogModule,
    ],
    template: `
    <div class="page-container">
      <h1 class="page-title">User Management</h1>
    
      <div class="toolbar">
        <mat-form-field appearance="outline" class="search-field">
          <mat-label>Search users</mat-label>
          <input matInput (keyup)="applyFilter($event)" placeholder="Search by name or email" #searchInput>
          <mat-icon matSuffix>search</mat-icon>
        </mat-form-field>
    
        <mat-form-field appearance="outline" class="filter-field">
          <mat-label>Role</mat-label>
          <mat-select [(value)]="roleFilter" (selectionChange)="applyFilters()">
            <mat-option value="">All Roles</mat-option>
            <mat-option value="admin">Admin</mat-option>
            <mat-option value="editor">Editor</mat-option>
            <mat-option value="viewer">Viewer</mat-option>
          </mat-select>
        </mat-form-field>
    
        <mat-form-field appearance="outline" class="filter-field">
          <mat-label>Status</mat-label>
          <mat-select [(value)]="statusFilter" (selectionChange)="applyFilters()">
            <mat-option value="">All Statuses</mat-option>
            <mat-option value="active">Active</mat-option>
            <mat-option value="suspended">Suspended</mat-option>
            <mat-option value="pending">Pending</mat-option>
          </mat-select>
        </mat-form-field>
      </div>
    
      @if (loading) {
        <div class="loading-container">
          <mat-spinner diameter="40"></mat-spinner>
        </div>
      }
    
      @if (!loading) {
        <div class="table-container">
          <table mat-table [dataSource]="dataSource" matSort class="users-table">
            <ng-container matColumnDef="displayName">
              <th mat-header-cell *matHeaderCellDef mat-sort-header>Name</th>
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
            <ng-container matColumnDef="role">
              <th mat-header-cell *matHeaderCellDef mat-sort-header>Role</th>
              <td mat-cell *matCellDef="let user">
                <span class="role-chip" [class]="'role-' + user.role">{{ user.role }}</span>
              </td>
            </ng-container>
            <ng-container matColumnDef="status">
              <th mat-header-cell *matHeaderCellDef mat-sort-header>Status</th>
              <td mat-cell *matCellDef="let user">
                <span class="status-chip" [class]="'status-' + user.status">{{ user.status }}</span>
              </td>
            </ng-container>
            <ng-container matColumnDef="department">
              <th mat-header-cell *matHeaderCellDef mat-sort-header>Department</th>
              <td mat-cell *matCellDef="let user">{{ user.department || '-' }}</td>
            </ng-container>
            <ng-container matColumnDef="lastLogin">
              <th mat-header-cell *matHeaderCellDef mat-sort-header>Last Login</th>
              <td mat-cell *matCellDef="let user">{{ user.lastLogin ? (user.lastLogin | date:'short') : 'Never' }}</td>
            </ng-container>
            <ng-container matColumnDef="actions">
              <th mat-header-cell *matHeaderCellDef>Actions</th>
              <td mat-cell *matCellDef="let user">
                <button mat-icon-button [matMenuTriggerFor]="actionMenu" aria-label="User actions" (click)="$event.stopPropagation()">
                  <mat-icon>more_vert</mat-icon>
                </button>
                <mat-menu #actionMenu="matMenu">
                  <button mat-menu-item (click)="viewUser(user)">
                    <mat-icon>visibility</mat-icon><span>View Details</span>
                  </button>
                  @if (user.status === 'active') {
                    <button mat-menu-item (click)="suspendUser(user)">
                      <mat-icon>block</mat-icon><span>Suspend</span>
                    </button>
                  }
                  @if (user.status === 'suspended') {
                    <button mat-menu-item (click)="restoreUser(user)">
                      <mat-icon>restore</mat-icon><span>Restore</span>
                    </button>
                  }
                  <button mat-menu-item class="delete-action" (click)="deleteUser(user)">
                    <mat-icon>delete</mat-icon><span>Delete</span>
                  </button>
                </mat-menu>
              </td>
            </ng-container>
            <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
            <tr mat-row *matRowDef="let row; columns: displayedColumns;" class="table-row" (click)="viewUser(row)"></tr>
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

    .toolbar { display: flex; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
    .search-field { flex: 1; min-width: 250px; }
    .filter-field { width: 160px; }

    .table-container {
      background: white;
      border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.08);
      overflow: hidden;
    }

    .users-table { width: 100%; }
    .table-row { cursor: pointer; }
    .table-row:hover { background: #f5f5f5; }

    .user-cell { display: flex; align-items: center; gap: 12px; padding: 4px 0; }
    .user-avatar { color: #bdbdbd; font-size: 36px; width: 36px; height: 36px; }
    .user-name { font-weight: 500; }
    .user-email { font-size: 0.8rem; color: #999; }

    .role-chip, .status-chip {
      padding: 4px 10px; border-radius: 12px; font-size: 0.75rem;
      font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
    }

    .role-admin { background: #e3f2fd; color: #1565c0; }
    .role-editor { background: #e8f5e9; color: #2e7d32; }
    .role-viewer { background: #f3e5f5; color: #7b1fa2; }

    .status-active { background: #e8f5e9; color: #2e7d32; }
    .status-suspended { background: #ffebee; color: #c62828; }
    .status-pending { background: #fff3e0; color: #e65100; }

    .delete-action { color: #d32f2f; }
  `]
})
export class UsersComponent implements OnInit {
  displayedColumns = ['displayName', 'role', 'status', 'department', 'lastLogin', 'actions'];
  dataSource = new MatTableDataSource<User>([]);
  loading = true;
  roleFilter = '';
  statusFilter = '';
  private searchFilter = '';

  @ViewChild(MatPaginator) set matPaginator(paginator: MatPaginator) {
    this.dataSource.paginator = paginator;
  }
  @ViewChild(MatSort) set matSort(sort: MatSort) {
    this.dataSource.sort = sort;
  }

  constructor(
    private api: AdminApiService,
    private router: Router,
    private snackBar: MatSnackBar,
    private dialog: MatDialog,
  ) {}

  ngOnInit(): void {
    this.loadUsers();
  }

  loadUsers(): void {
    this.loading = true;
    this.api.getUsers().subscribe(users => {
      this.dataSource.data = users;
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
    this.dataSource.filterPredicate = (data: User) => {
      const matchesSearch = !this.searchFilter ||
        data.displayName.toLowerCase().includes(this.searchFilter) ||
        data.email.toLowerCase().includes(this.searchFilter);
      const matchesRole = !this.roleFilter || data.role === this.roleFilter;
      const matchesStatus = !this.statusFilter || data.status === this.statusFilter;
      return matchesSearch && matchesRole && matchesStatus;
    };
  }

  viewUser(user: User): void {
    this.router.navigate(['/users', user.id]);
  }

  suspendUser(user: User): void {
    const dialogRef = this.dialog.open(ConfirmDialogComponent, {
      data: { title: 'Suspend User', message: `Are you sure you want to suspend ${user.displayName}?`, confirmText: 'Suspend', confirmColor: 'warn' },
    });
    dialogRef.afterClosed().subscribe(result => {
      if (result) {
        this.api.suspendUser(user.id).subscribe(() => {
          this.snackBar.open(`${user.displayName} has been suspended`, 'Dismiss', { duration: 3000 });
          this.loadUsers();
        });
      }
    });
  }

  restoreUser(user: User): void {
    this.api.restoreUser(user.id).subscribe(() => {
      this.snackBar.open(`${user.displayName} has been restored`, 'Dismiss', { duration: 3000 });
      this.loadUsers();
    });
  }

  deleteUser(user: User): void {
    const dialogRef = this.dialog.open(ConfirmDialogComponent, {
      data: { title: 'Delete User', message: `Are you sure you want to permanently delete ${user.displayName}? This action cannot be undone.`, confirmText: 'Delete', confirmColor: 'warn' },
    });
    dialogRef.afterClosed().subscribe(result => {
      if (result) {
        this.api.deleteUser(user.id).subscribe(() => {
          this.snackBar.open(`${user.displayName} has been deleted`, 'Dismiss', { duration: 3000 });
          this.loadUsers();
        });
      }
    });
  }
}
