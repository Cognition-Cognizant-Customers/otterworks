import { Component } from '@angular/core';

import { MatToolbarModule } from '@angular/material/toolbar';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { AuthService } from '../../core/services/auth.service';

@Component({
    selector: 'app-toolbar',
    imports: [MatToolbarModule, MatIconModule, MatButtonModule, MatMenuModule],
    template: `
    <mat-toolbar class="app-toolbar">
      <span class="toolbar-title">Admin Dashboard</span>
      <span class="spacer"></span>
      @if (authService.currentUser; as user) {
        <span class="user-info">
          <mat-icon>account_circle</mat-icon>
          <span class="user-name">{{ user.displayName }}</span>
        </span>
      }
      <button mat-icon-button [matMenuTriggerFor]="userMenu" aria-label="User menu">
        <mat-icon>more_vert</mat-icon>
      </button>
      <mat-menu #userMenu="matMenu">
        <button mat-menu-item (click)="authService.logout()">
          <mat-icon>logout</mat-icon>
          <span>Logout</span>
        </button>
      </mat-menu>
    </mat-toolbar>
    `,
    styles: [`
    .app-toolbar {
      background: #ffffff;
      color: #333;
      border-bottom: 1px solid #e0e0e0;
      height: 64px;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
    }

    .toolbar-title {
      font-size: 1.1rem;
      font-weight: 500;
    }

    .spacer {
      flex: 1;
    }

    .user-info {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-right: 8px;
      color: #666;
    }

    .user-name {
      font-size: 0.9rem;
    }
  `]
})
export class ToolbarComponent {
  constructor(public authService: AuthService) {}
}
