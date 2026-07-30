import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterOutlet } from '@angular/router';
import { SidebarComponent } from './layout/sidebar/sidebar.component';
import { ToolbarComponent } from './layout/toolbar/toolbar.component';
import { AuthService } from './core/services/auth.service';

@Component({
    selector: 'app-root',
    imports: [CommonModule, RouterOutlet, SidebarComponent, ToolbarComponent],
    template: `
    <div class="app-layout" *ngIf="authService.isAuthenticated; else loginView">
      <app-sidebar [(collapsed)]="sidebarCollapsed"></app-sidebar>
      <div class="main-area">
        <app-toolbar></app-toolbar>
        <main class="content" [class.sidebar-collapsed]="sidebarCollapsed">
          <router-outlet></router-outlet>
        </main>
      </div>
    </div>
    <ng-template #loginView>
      <router-outlet></router-outlet>
    </ng-template>
  `,
    styles: [`
    .app-layout {
      display: flex;
      min-height: 100vh;
    }

    .main-area {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }

    .content {
      flex: 1;
      padding: 24px;
      background: #f5f5f5;
      overflow-y: auto;
    }
  `]
})
export class AppComponent {
  sidebarCollapsed = false;

  constructor(public authService: AuthService) {}
}
