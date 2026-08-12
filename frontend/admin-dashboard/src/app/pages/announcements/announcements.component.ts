import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { AdminApiService } from '../../core/services/admin-api.service';
import { Announcement } from '../../core/models/announcement.model';
import { ConfirmDialogComponent } from '../../shared/components/confirm-dialog.component';

@Component({
    selector: 'app-announcements',
    imports: [
        CommonModule, FormsModule, MatCardModule, MatIconModule, MatButtonModule,
        MatProgressSpinnerModule, MatChipsModule, MatInputModule, MatFormFieldModule,
        MatSelectModule, MatSnackBarModule, MatDialogModule,
    ],
    template: `
    <div class="page-container">
      <div class="page-header">
        <h1 class="page-title">Announcements</h1>
        <button mat-raised-button color="primary" (click)="showCreateForm = !showCreateForm">
          <mat-icon>{{ showCreateForm ? 'close' : 'add' }}</mat-icon>
          {{ showCreateForm ? 'Cancel' : 'New Announcement' }}
        </button>
      </div>
    
      @if (showCreateForm) {
        <mat-card class="create-form">
          <mat-card-header>
            <mat-card-title>Create Announcement</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Title</mat-label>
              <input matInput [(ngModel)]="newAnnouncement.title" placeholder="Announcement title">
            </mat-form-field>
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Content</mat-label>
              <textarea matInput [(ngModel)]="newAnnouncement.content" rows="4" placeholder="Announcement content"></textarea>
            </mat-form-field>
            <div class="form-row">
              <mat-form-field appearance="outline">
                <mat-label>Priority</mat-label>
                <mat-select [(ngModel)]="newAnnouncement.priority">
                  <mat-option value="low">Low</mat-option>
                  <mat-option value="medium">Medium</mat-option>
                  <mat-option value="high">High</mat-option>
                  <mat-option value="critical">Critical</mat-option>
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Target Audience</mat-label>
                <mat-select [(ngModel)]="newAnnouncement.targetAudience">
                  <mat-option value="all">All Users</mat-option>
                  <mat-option value="admins">Admins Only</mat-option>
                  <mat-option value="editors">Editors</mat-option>
                  <mat-option value="viewers">Viewers</mat-option>
                </mat-select>
              </mat-form-field>
            </div>
            <button mat-raised-button color="primary" (click)="createAnnouncement()" [disabled]="!newAnnouncement.title || !newAnnouncement.content">
              Create
            </button>
          </mat-card-content>
        </mat-card>
      }
    
      @if (loading) {
        <div class="loading-container">
          <mat-spinner diameter="40"></mat-spinner>
        </div>
      }
    
      @if (!loading) {
        <div class="announcements-list">
          @for (ann of announcements; track ann) {
            <mat-card class="announcement-card" [class]="'priority-border-' + ann.priority">
              <mat-card-content>
                <div class="ann-header">
                  <div>
                    <h3>{{ ann.title }}</h3>
                    <div class="ann-badges">
                      <span class="priority-chip" [class]="'priority-' + ann.priority">{{ ann.priority }}</span>
                      <span class="status-chip" [class]="'ann-status-' + ann.status">{{ ann.status }}</span>
                      <span class="audience-chip">
                        <mat-icon>group</mat-icon>
                        {{ ann.targetAudience }}
                      </span>
                    </div>
                  </div>
                  <div class="ann-actions">
                    @if (ann.status === 'draft') {
                      <button mat-icon-button (click)="publishAnnouncement(ann)" color="primary" aria-label="Publish">
                        <mat-icon>publish</mat-icon>
                      </button>
                    }
                    <button mat-icon-button (click)="deleteAnnouncement(ann)" color="warn" aria-label="Delete">
                      <mat-icon>delete</mat-icon>
                    </button>
                  </div>
                </div>
                <p class="ann-content">{{ ann.content }}</p>
                <div class="ann-meta">
                  <span>By {{ ann.createdBy }} on {{ ann.createdAt | date:'mediumDate' }}</span>
                  @if (ann.publishedAt) {
                    <span>Published: {{ ann.publishedAt | date:'medium' }}</span>
                  }
                  @if (ann.expiresAt) {
                    <span>Expires: {{ ann.expiresAt | date:'mediumDate' }}</span>
                  }
                </div>
              </mat-card-content>
            </mat-card>
          }
        </div>
      }
    
      @if (!loading && announcements.length === 0) {
        <div class="empty-state">
          <mat-icon>campaign</mat-icon>
          <p>No announcements yet</p>
        </div>
      }
    </div>
    `,
    styles: [`
    .page-container { padding: 0; }
    .page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
    .page-title { font-size: 1.5rem; font-weight: 600; color: #333; margin: 0; }
    .loading-container { display: flex; justify-content: center; padding: 60px; }

    .create-form { margin-bottom: 24px; }
    .full-width { width: 100%; }
    .form-row { display: flex; gap: 16px; }

    .announcements-list { display: flex; flex-direction: column; gap: 16px; }

    .announcement-card { border-left: 4px solid transparent; }
    .priority-border-low { border-left-color: #4caf50; }
    .priority-border-medium { border-left-color: #2196f3; }
    .priority-border-high { border-left-color: #ff9800; }
    .priority-border-critical { border-left-color: #f44336; }

    .ann-header { display: flex; justify-content: space-between; align-items: flex-start; }
    .ann-header h3 { margin: 0 0 8px; font-size: 1.1rem; }
    .ann-badges { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .ann-actions { display: flex; gap: 4px; }

    .priority-chip, .status-chip, .audience-chip {
      padding: 3px 8px; border-radius: 4px; font-size: 0.7rem;
      font-weight: 600; text-transform: uppercase;
    }

    .priority-low { background: #e8f5e9; color: #2e7d32; }
    .priority-medium { background: #e3f2fd; color: #1565c0; }
    .priority-high { background: #fff3e0; color: #e65100; }
    .priority-critical { background: #ffebee; color: #c62828; }

    .ann-status-draft { background: #eceff1; color: #546e7a; }
    .ann-status-published { background: #e8f5e9; color: #2e7d32; }
    .ann-status-archived { background: #f5f5f5; color: #999; }

    .audience-chip {
      display: flex; align-items: center; gap: 4px;
      background: #f3e5f5; color: #7b1fa2;
    }

    .audience-chip .mat-icon { font-size: 14px; width: 14px; height: 14px; }

    .ann-content { color: #555; line-height: 1.6; margin: 16px 0; }

    .ann-meta { display: flex; gap: 16px; font-size: 0.8rem; color: #999; flex-wrap: wrap; }

    .empty-state {
      display: flex; flex-direction: column; align-items: center; padding: 60px; color: #999;
    }

    .empty-state .mat-icon { font-size: 48px; width: 48px; height: 48px; margin-bottom: 12px; }
  `]
})
export class AnnouncementsComponent implements OnInit {
  announcements: Announcement[] = [];
  loading = true;
  showCreateForm = false;
  newAnnouncement: Partial<Announcement> = { priority: 'medium', targetAudience: 'all' };

  constructor(
    private api: AdminApiService,
    private snackBar: MatSnackBar,
    private dialog: MatDialog,
  ) {}

  ngOnInit(): void {
    this.loadAnnouncements();
  }

  loadAnnouncements(): void {
    this.loading = true;
    this.api.getAnnouncements().subscribe(announcements => {
      this.announcements = announcements;
      this.loading = false;
    });
  }

  createAnnouncement(): void {
    this.api.createAnnouncement(this.newAnnouncement).subscribe(() => {
      this.snackBar.open('Announcement created', 'Dismiss', { duration: 3000 });
      this.showCreateForm = false;
      this.newAnnouncement = { priority: 'medium', targetAudience: 'all' };
      this.loadAnnouncements();
    });
  }

  publishAnnouncement(ann: Announcement): void {
    this.api.publishAnnouncement(ann.id).subscribe(() => {
      this.snackBar.open('Announcement published', 'Dismiss', { duration: 3000 });
      this.loadAnnouncements();
    });
  }

  deleteAnnouncement(ann: Announcement): void {
    const dialogRef = this.dialog.open(ConfirmDialogComponent, {
      data: { title: 'Delete Announcement', message: `Delete "${ann.title}"?`, confirmText: 'Delete', confirmColor: 'warn' },
    });
    dialogRef.afterClosed().subscribe(result => {
      if (result) {
        this.api.deleteAnnouncement(ann.id).subscribe(() => {
          this.snackBar.open('Announcement deleted', 'Dismiss', { duration: 3000 });
          this.loadAnnouncements();
        });
      }
    });
  }
}
