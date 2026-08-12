import { Component, inject } from '@angular/core';

import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';

export interface ConfirmDialogData {
  title: string;
  message: string;
  confirmText: string;
  confirmColor: string;
}

@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  imports: [MatDialogModule, MatButtonModule],
  template: `
    <h2 mat-dialog-title>{{ data.title }}</h2>
    <mat-dialog-content>
      <p>{{ data.message }}</p>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close>Cancel</button>
      <button mat-raised-button [color]="data.confirmColor" [mat-dialog-close]="true">
        {{ data.confirmText }}
      </button>
    </mat-dialog-actions>
  `,
  styles: [`
    p { margin: 0; color: #666; line-height: 1.6; }
  `],
})
export class ConfirmDialogComponent {
  protected data = inject<ConfirmDialogData>(MAT_DIALOG_DATA);
}
