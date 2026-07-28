import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatFormFieldModule } from '@angular/material/form-field';
import { AuthService } from '../../core/services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [
    CommonModule, FormsModule, MatCardModule, MatInputModule,
    MatButtonModule, MatIconModule, MatProgressSpinnerModule, MatFormFieldModule,
  ],
  template: `
    <div class="login-container">
      <mat-card class="login-card">
        <mat-card-header>
          <div class="login-header">
            <img class="login-logo" src="assets/otter-logo.png" alt="OtterWorks logo" width="64" height="64" />
            <h1>OtterWorks Admin</h1>
            <p>Sign in to the admin dashboard</p>
          </div>
        </mat-card-header>
        <mat-card-content>
          <form (ngSubmit)="onLogin()" class="login-form">
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Email</mat-label>
              <input matInput type="email" [(ngModel)]="email" name="email" required placeholder="admin@otterworks.io" (input)="clearError()">
              <mat-icon matSuffix>email</mat-icon>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Password</mat-label>
              <input matInput [type]="hidePassword ? 'password' : 'text'" [(ngModel)]="password" name="password" required (input)="clearError()">
              <button mat-icon-button matSuffix type="button" (click)="hidePassword = !hidePassword">
                <mat-icon>{{ hidePassword ? 'visibility_off' : 'visibility' }}</mat-icon>
              </button>
            </mat-form-field>

            <div class="error-message" *ngIf="errorMessage">
              <mat-icon>error</mat-icon>
              {{ errorMessage }}
            </div>

            <button mat-raised-button color="primary" type="submit" class="login-btn" [disabled]="loading">
              <mat-spinner *ngIf="loading" diameter="20"></mat-spinner>
              <span *ngIf="!loading">Sign In</span>
            </button>
          </form>
        </mat-card-content>
      </mat-card>
    </div>
  `,
  styles: [`
    .login-container {
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      background: #1f3a5f;
    }

    .login-card {
      width: 420px;
      padding: 40px;
    }

    .login-header {
      text-align: center;
      width: 100%;
      margin-bottom: 24px;
    }

    .login-logo {
      width: 64px;
      height: 64px;
      margin-bottom: 16px;
    }

    .login-header h1 {
      margin: 0;
      font-size: 1.5rem;
      color: #333;
    }

    .login-header p {
      margin: 8px 0 0;
      color: #666;
      font-size: 0.9rem;
    }

    :host ::ng-deep .mat-mdc-card-header {
      display: block;
    }

    .login-form {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .full-width {
      width: 100%;
    }

    .login-btn {
      width: 100%;
      height: 48px;
      font-size: 1rem;
      margin-top: 8px;
    }

    .error-message {
      display: flex;
      align-items: center;
      gap: 8px;
      color: #f44336;
      font-size: 0.85rem;
      margin-bottom: 8px;
    }
  `],
})
export class LoginComponent {
  email = '';
  password = '';
  hidePassword = true;
  loading = false;
  errorMessage = '';

  constructor(private authService: AuthService, private router: Router) {
    if (this.authService.isAuthenticated) {
      this.router.navigate(['/']);
    }
  }

  clearError(): void {
    if (this.errorMessage) {
      this.errorMessage = '';
    }
  }

  onLogin(): void {
    if (!this.email || !this.password) {
      this.errorMessage = 'Please enter both email and password.';
      return;
    }

    this.loading = true;
    this.errorMessage = '';

    this.authService.login(this.email, this.password).subscribe({
      next: () => {
        this.router.navigate(['/']);
      },
      error: (err: Error) => {
        this.loading = false;
        this.errorMessage = err.message || 'Login failed. Please try again.';
      },
    });
  }
}
