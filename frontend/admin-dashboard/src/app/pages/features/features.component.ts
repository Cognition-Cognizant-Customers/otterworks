import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { AdminApiService } from '../../core/services/admin-api.service';
import { FeatureFlag } from '../../core/models/feature-flag.model';

@Component({
    selector: 'app-features',
    imports: [
        CommonModule, FormsModule, MatCardModule, MatIconModule, MatSlideToggleModule,
        MatProgressSpinnerModule, MatChipsModule, MatInputModule, MatFormFieldModule,
        MatSnackBarModule,
    ],
    template: `
    <div class="page-container">
      <h1 class="page-title">Feature Flags</h1>
    
      <mat-form-field appearance="outline" class="search-field">
        <mat-label>Search features</mat-label>
        <input matInput [(ngModel)]="searchTerm" placeholder="Search by name or description">
        <mat-icon matSuffix>search</mat-icon>
      </mat-form-field>
    
      @if (loading) {
        <div class="loading-container">
          <mat-spinner diameter="40"></mat-spinner>
        </div>
      }
    
      @if (!loading) {
        <div class="flags-grid">
          @for (flag of filteredFlags; track flag) {
            <mat-card class="flag-card">
              <mat-card-content>
                <div class="flag-header">
                  <div class="flag-info">
                    <h3>{{ flag.name }}</h3>
                    <span class="flag-key">{{ flag.key }}</span>
                  </div>
                  <mat-slide-toggle
                    [checked]="flag.enabled"
                    (change)="toggleFlag(flag)"
                    [color]="'primary'">
                  </mat-slide-toggle>
                </div>
                <p class="flag-description">{{ flag.description }}</p>
                <div class="flag-meta">
                  <span class="flag-category">
                    <mat-icon>label</mat-icon>
                    {{ flag.category }}
                  </span>
                  <span class="flag-updated">
                    Updated {{ flag.updatedAt | date:'shortDate' }} by {{ flag.updatedBy }}
                  </span>
                </div>
              </mat-card-content>
            </mat-card>
          }
        </div>
      }
    
      @if (!loading && filteredFlags.length === 0) {
        <div class="empty-state">
          <mat-icon>toggle_off</mat-icon>
          <p>No feature flags match your search</p>
        </div>
      }
    </div>
    `,
    styles: [`
    .page-container { padding: 0; }
    .page-title { font-size: 1.5rem; font-weight: 600; color: #333; margin-bottom: 24px; }
    .loading-container { display: flex; justify-content: center; padding: 60px; }
    .search-field { width: 100%; max-width: 400px; margin-bottom: 16px; }

    .flags-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
      gap: 16px;
    }

    .flag-card { transition: box-shadow 0.2s; }
    .flag-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.12); }

    .flag-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
    .flag-info h3 { margin: 0 0 4px; font-size: 1rem; }
    .flag-key { font-family: monospace; font-size: 0.8rem; color: #999; background: #f5f5f5; padding: 2px 6px; border-radius: 4px; }

    .flag-description { color: #666; font-size: 0.9rem; line-height: 1.5; margin: 0 0 16px; }

    .flag-meta { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }

    .flag-category {
      display: flex; align-items: center; gap: 4px;
      font-size: 0.8rem; color: #1976d2; font-weight: 500;
    }

    .flag-category .mat-icon { font-size: 16px; width: 16px; height: 16px; }
    .flag-updated { font-size: 0.75rem; color: #999; }

    .empty-state {
      display: flex; flex-direction: column; align-items: center; padding: 60px; color: #999;
    }

    .empty-state .mat-icon { font-size: 48px; width: 48px; height: 48px; margin-bottom: 12px; }
  `]
})
export class FeaturesComponent implements OnInit {
  flags: FeatureFlag[] = [];
  loading = true;
  searchTerm = '';

  constructor(private api: AdminApiService, private snackBar: MatSnackBar) {}

  ngOnInit(): void {
    this.api.getFeatureFlags().subscribe(flags => {
      this.flags = flags;
      this.loading = false;
    });
  }

  get filteredFlags(): FeatureFlag[] {
    if (!this.searchTerm) return this.flags;
    const term = this.searchTerm.toLowerCase();
    return this.flags.filter(f =>
      f.name.toLowerCase().includes(term) ||
      f.description.toLowerCase().includes(term) ||
      f.key.toLowerCase().includes(term)
    );
  }

  toggleFlag(flag: FeatureFlag): void {
    const newState = !flag.enabled;
    this.api.toggleFeatureFlag(flag.id, newState).subscribe(() => {
      this.snackBar.open(
        `${flag.name} ${newState ? 'enabled' : 'disabled'}`,
        'Dismiss',
        { duration: 3000 },
      );
    });
  }
}
