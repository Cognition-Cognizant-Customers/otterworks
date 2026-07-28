import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

interface NavItem {
  label: string;
  icon: string;
  route: string;
}

@Component({
  selector: 'app-sidebar',
  standalone: true,
  imports: [CommonModule, RouterModule, MatListModule, MatIconModule, MatTooltipModule],
  template: `
    <div class="sidebar" [class.collapsed]="collapsed">
      <div class="sidebar-header">
        <div class="logo" *ngIf="!collapsed">
          <img class="logo-icon" src="assets/otter-logo.png" alt="OtterWorks logo" width="28" height="28" />
          <span class="logo-text">OtterWorks</span>
        </div>
        <div class="logo logo-collapsed" *ngIf="collapsed">
          <img class="logo-icon" src="assets/otter-logo.png" alt="OtterWorks logo" width="28" height="28" />
        </div>
        <button class="toggle-btn" (click)="toggleCollapsed()" [matTooltip]="collapsed ? 'Expand' : 'Collapse'">
          <mat-icon>{{ collapsed ? 'chevron_right' : 'chevron_left' }}</mat-icon>
        </button>
      </div>

      <mat-nav-list>
        <a mat-list-item
           *ngFor="let item of navItems"
           [routerLink]="item.route"
           routerLinkActive="active-link"
           [routerLinkActiveOptions]="{ exact: item.route === '/' }"
           [matTooltip]="collapsed ? item.label : ''"
           matTooltipPosition="right">
          <mat-icon matListItemIcon>{{ item.icon }}</mat-icon>
          <span matListItemTitle *ngIf="!collapsed">{{ item.label }}</span>
        </a>
      </mat-nav-list>
    </div>
  `,
  styles: [`
    .sidebar {
      width: 260px;
      min-height: 100vh;
      background: #1f3a5f;
      color: #ffffff;
      display: flex;
      flex-direction: column;
      transition: width 0.3s ease;
      overflow: hidden;
    }

    .sidebar.collapsed {
      width: 68px;
    }

    .sidebar-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      min-height: 64px;
    }

    .logo {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .logo-collapsed {
      margin: 0 auto;
    }

    .logo-icon {
      width: 28px;
      height: 28px;
      background: #ffffff;
      border-radius: 2px;
    }

    .logo-text {
      font-size: 1.15rem;
      font-weight: 600;
      white-space: nowrap;
    }

    .toggle-btn {
      background: none;
      border: none;
      color: rgba(255, 255, 255, 0.7);
      cursor: pointer;
      padding: 4px;
      border-radius: 4px;
      display: flex;
      align-items: center;
    }

    .toggle-btn:hover {
      background: rgba(255, 255, 255, 0.1);
      color: #ffffff;
    }

    .sidebar.collapsed .toggle-btn {
      margin: 0 auto;
    }

    :host ::ng-deep .mat-mdc-nav-list {
      padding-top: 8px;
    }

    :host ::ng-deep .mat-mdc-list-item {
      color: rgba(255, 255, 255, 0.75) !important;
      margin: 2px 8px;
      border-radius: 2px;
    }

    :host ::ng-deep .mat-mdc-list-item .mdc-list-item__primary-text,
    :host ::ng-deep .mat-mdc-list-item .mat-mdc-list-item-title {
      color: rgba(255, 255, 255, 0.75) !important;
    }

    :host ::ng-deep .mat-mdc-list-item:hover {
      background: rgba(255, 255, 255, 0.08) !important;
      color: #ffffff !important;
    }

    :host ::ng-deep .mat-mdc-list-item:hover .mdc-list-item__primary-text,
    :host ::ng-deep .mat-mdc-list-item:hover .mat-mdc-list-item-title {
      color: #ffffff !important;
    }

    :host ::ng-deep .active-link {
      background: rgba(217, 154, 61, 0.18) !important;
      color: #d99a3d !important;
    }

    :host ::ng-deep .active-link .mdc-list-item__primary-text,
    :host ::ng-deep .active-link .mat-mdc-list-item-title {
      color: #d99a3d !important;
    }

    :host ::ng-deep .active-link .mat-icon {
      color: #d99a3d !important;
    }

    :host ::ng-deep .mat-mdc-list-item .mat-icon {
      color: rgba(255, 255, 255, 0.75);
    }
  `],
})
export class SidebarComponent {
  @Input() collapsed = false;
  @Output() collapsedChange = new EventEmitter<boolean>();

  navItems: NavItem[] = [
    { label: 'Dashboard', icon: 'dashboard', route: '/' },
    { label: 'Users', icon: 'people', route: '/users' },
    { label: 'Audit Logs', icon: 'history', route: '/audit' },
    { label: 'Feature Flags', icon: 'toggle_on', route: '/features' },
    { label: 'System Health', icon: 'monitor_heart', route: '/health' },
    { label: 'Announcements', icon: 'campaign', route: '/announcements' },
    { label: 'Storage Quotas', icon: 'storage', route: '/quotas' },
    { label: 'Incidents', icon: 'report_problem', route: '/incidents' },
    { label: 'Analytics', icon: 'bar_chart', route: '/analytics' },
  ];

  toggleCollapsed(): void {
    this.collapsed = !this.collapsed;
    this.collapsedChange.emit(this.collapsed);
  }
}
