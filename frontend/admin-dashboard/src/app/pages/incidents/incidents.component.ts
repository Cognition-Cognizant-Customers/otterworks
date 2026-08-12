import { Component, OnInit, OnDestroy } from '@angular/core';
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
import { MatBadgeModule } from '@angular/material/badge';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { AdminApiService } from '../../core/services/admin-api.service';
import { Incident, AFFECTED_SERVICES } from '../../core/models/incident.model';
import { ConfirmDialogComponent } from '../../shared/components/confirm-dialog.component';
import { Subscription, interval } from 'rxjs';

const CHAOS_STATE_KEY = 'ow_admin_chaos_state';

@Component({
    selector: 'app-incidents',
    imports: [
        CommonModule, FormsModule, MatCardModule, MatIconModule, MatButtonModule,
        MatProgressSpinnerModule, MatChipsModule, MatInputModule, MatFormFieldModule,
        MatSelectModule, MatSnackBarModule, MatBadgeModule, MatTooltipModule, MatSlideToggleModule,
        MatDialogModule,
    ],
    template: `
    <div class="page-container">
      <div class="page-header">
        <div>
          <h1 class="page-title">Incident Response</h1>
          <p class="page-subtitle">Create incident tickets to trigger automated Devin investigation sessions</p>
        </div>
        <button mat-raised-button color="warn" (click)="showCreateForm = !showCreateForm">
          <mat-icon>{{ showCreateForm ? 'close' : 'add_alert' }}</mat-icon>
          {{ showCreateForm ? 'Cancel' : 'Report Incident' }}
        </button>
      </div>
    
      <!-- Demo Controls: chaos injection panel for workshop use -->
      <mat-card class="demo-controls" [class.expanded]="showDemoControls">
        <mat-card-header (click)="showDemoControls = !showDemoControls" style="cursor:pointer">
          <mat-card-title>
            <mat-icon>science</mat-icon>
            Demo Controls
            @if (activeChaosCount > 0) {
              <span class="demo-badge">{{ activeChaosCount }} active</span>
            }
          </mat-card-title>
          <mat-card-subtitle>Inject realistic failures and control the investigation flow: manual, one-click, or fully automatic</mat-card-subtitle>
        </mat-card-header>
        @if (showDemoControls) {
          <mat-card-content>
            <div class="auto-investigate-toggle">
              <div class="toggle-info">
                <div class="toggle-label">
                  <mat-icon>smart_toy</mat-icon>
                  <span>Auto-Investigate with Devin</span>
                </div>
                @if (autoInvestigate) {
                  <div class="toggle-description">
                    <strong>ON:</strong> Grafana alerts auto-create incidents AND launch Devin sessions (Flow 3: fully automatic)
                  </div>
                }
                @if (!autoInvestigate) {
                  <div class="toggle-description">
                    <strong>OFF:</strong> Grafana alerts auto-create incidents but do NOT launch Devin. Use "Launch Devin" button on individual incidents (Flow 2) or investigate manually via Grafana (Flow 1)
                  </div>
                }
              </div>
              <mat-slide-toggle
                [checked]="autoInvestigate"
                [disabled]="autoInvestigateLoading"
                (change)="toggleAutoInvestigate($event.checked)"
                color="primary">
              </mat-slide-toggle>
            </div>
            <div class="chaos-grid">
              <div class="chaos-scenario" [class.chaos-active]="chaosState['search-service']">
                <div class="chaos-scenario-header">
                  <mat-icon class="chaos-svc-icon">search</mat-icon>
                  <div>
                    <div class="chaos-svc-name">Search Service <span class="chaos-lang">Python/Flask</span></div>
                    <div class="chaos-svc-desc">Autocomplete suggest endpoint crashes with KeyError on ranking score enrichment → 500s</div>
                  </div>
                </div>
                @if (chaosState['search-service']) {
                  <div class="chaos-status">
                    <mat-icon class="chaos-active-icon">bolt</mat-icon>
                    Chaos active — Grafana alert fires in ~30s
                  </div>
                }
                <button mat-raised-button color="warn" (click)="triggerChaos('search-service', 'suggest_500')"
                  [disabled]="!!chaosState['search-service'] || chaosLoading">
                  <mat-icon>{{ chaosState['search-service'] ? 'check' : 'bug_report' }}</mat-icon>
                  {{ chaosState['search-service'] ? 'Breaking...' : 'Break Search Autocomplete' }}
                </button>
              </div>
              <div class="chaos-scenario" [class.chaos-active]="chaosState['file-service']">
                <div class="chaos-scenario-header">
                  <mat-icon class="chaos-svc-icon">cloud_upload</mat-icon>
                  <div>
                    <div class="chaos-svc-name">File Service <span class="chaos-lang">Rust/Actix-Web</span></div>
                    <div class="chaos-svc-desc">Uploads routed to nonexistent S3 bucket → AWS NoSuchBucket errors → 500s</div>
                  </div>
                </div>
                @if (chaosState['file-service']) {
                  <div class="chaos-status">
                    <mat-icon class="chaos-active-icon">bolt</mat-icon>
                    Chaos active — Grafana alert fires in ~30s
                  </div>
                }
                <button mat-raised-button color="warn" (click)="triggerChaos('file-service', 'upload_s3_error')"
                  [disabled]="!!chaosState['file-service'] || chaosLoading">
                  <mat-icon>{{ chaosState['file-service'] ? 'check' : 'bug_report' }}</mat-icon>
                  {{ chaosState['file-service'] ? 'Breaking...' : 'Break File Uploads' }}
                </button>
              </div>
              <div class="chaos-scenario" [class.chaos-active]="chaosState['notification-service']">
                <div class="chaos-scenario-header">
                  <mat-icon class="chaos-svc-icon">notifications</mat-icon>
                  <div>
                    <div class="chaos-svc-name">Notification Service <span class="chaos-lang">Kotlin/Ktor</span></div>
                    <div class="chaos-svc-desc">Strict JSON schema rejects legacy message timestamps → deserialization loop → queue backlog grows</div>
                  </div>
                </div>
                @if (chaosState['notification-service']) {
                  <div class="chaos-status">
                    <mat-icon class="chaos-active-icon">bolt</mat-icon>
                    Chaos active — Grafana alert fires in ~2m
                  </div>
                }
                <button mat-raised-button color="warn" (click)="triggerChaos('notification-service', 'consumer_strict_schema')"
                  [disabled]="!!chaosState['notification-service'] || chaosLoading">
                  <mat-icon>{{ chaosState['notification-service'] ? 'check' : 'bug_report' }}</mat-icon>
                  {{ chaosState['notification-service'] ? 'Breaking...' : 'Break Notification Queue' }}
                </button>
              </div>
              <div class="chaos-scenario" [class.chaos-active]="chaosState['document-service']">
                <div class="chaos-scenario-header">
                  <mat-icon class="chaos-svc-icon">description</mat-icon>
                  <div>
                    <div class="chaos-svc-name">Document Service <span class="chaos-lang">Python/FastAPI</span></div>
                    <div class="chaos-svc-desc">Injected 3-5s latency before every database query → P95 latency spike → timeout cascade</div>
                  </div>
                </div>
                @if (chaosState['document-service']) {
                  <div class="chaos-status">
                    <mat-icon class="chaos-active-icon">bolt</mat-icon>
                    Chaos active — Grafana alert fires in ~1m
                  </div>
                }
                <button mat-raised-button color="warn" (click)="triggerChaos('document-service', 'slow_queries')"
                  [disabled]="!!chaosState['document-service'] || chaosLoading">
                  <mat-icon>{{ chaosState['document-service'] ? 'check' : 'bug_report' }}</mat-icon>
                  {{ chaosState['document-service'] ? 'Slowing...' : 'Inject Query Latency' }}
                </button>
              </div>
            </div>
            <div class="chaos-footer">
              <span class="chaos-note">Chaos flags auto-expire after 10 minutes</span>
              <button mat-stroked-button color="warn" (click)="resetAllChaos()" [disabled]="activeChaosCount === 0 || chaosLoading">
                <mat-icon>refresh</mat-icon>
                Reset All
              </button>
            </div>
          </mat-card-content>
        }
      </mat-card>
    
      <!-- Status summary chips (only show when there are incidents) -->
      @if (!loading && incidents.length > 0) {
        <div class="status-summary">
          <div class="summary-chip active" (click)="filterStatus = filterStatus === 'active' ? '' : 'active'">
            <mat-icon>warning</mat-icon>
            <span>{{ activeCount }} Active</span>
          </div>
          <div class="summary-chip investigating" (click)="filterStatus = filterStatus === 'investigating' ? '' : 'investigating'">
            <mat-icon>smart_toy</mat-icon>
            <span>{{ investigatingCount }} Devin Investigating</span>
          </div>
          <div class="summary-chip resolved" (click)="filterStatus = filterStatus === 'resolved' ? '' : 'resolved'">
            <mat-icon>check_circle</mat-icon>
            <span>{{ resolvedCount }} Resolved</span>
          </div>
          @if (closedCount > 0) {
            <div class="summary-chip closed" (click)="showClosed = !showClosed">
              <mat-icon>{{ showClosed ? 'visibility' : 'visibility_off' }}</mat-icon>
              <span>{{ closedCount }} Closed{{ showClosed ? '' : ' (hidden)' }}</span>
            </div>
          }
        </div>
      }
    
      <!-- Create form -->
      @if (showCreateForm) {
        <mat-card class="create-form">
          <mat-card-header>
            <mat-card-title>
              <mat-icon>add_alert</mat-icon>
              Report New Incident
            </mat-card-title>
            <mat-card-subtitle>A Devin session will automatically be created to investigate this incident</mat-card-subtitle>
          </mat-card-header>
          <mat-card-content>
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Incident Title</mat-label>
              <input matInput [(ngModel)]="newIncident.title" placeholder="Brief description of the issue">
            </mat-form-field>
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Description</mat-label>
              <textarea matInput [(ngModel)]="newIncident.description" rows="4"
              placeholder="Detailed description: what's happening, error messages, affected users..."></textarea>
            </mat-form-field>
            <div class="form-row">
              <mat-form-field appearance="outline">
                <mat-label>Severity</mat-label>
                <mat-select [(ngModel)]="newIncident.severity">
                  <mat-option value="low">Low</mat-option>
                  <mat-option value="medium">Medium</mat-option>
                  <mat-option value="high">High</mat-option>
                  <mat-option value="critical">Critical</mat-option>
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Affected Service</mat-label>
                <mat-select [(ngModel)]="newIncident.affectedService">
                  @for (svc of affectedServices; track svc) {
                    <mat-option [value]="svc.value">
                      {{ svc.label }}
                    </mat-option>
                  }
                </mat-select>
              </mat-form-field>
            </div>
            <button mat-raised-button color="warn" (click)="createIncident()"
              [disabled]="!newIncident.title || !newIncident.description || creating">
              <mat-icon>{{ creating ? 'hourglass_empty' : 'smart_toy' }}</mat-icon>
              {{ creating ? 'Creating Devin Session...' : 'Create Incident & Launch Devin' }}
            </button>
          </mat-card-content>
        </mat-card>
      }
    
      <!-- Loading -->
      @if (loading) {
        <div class="loading-container">
          <mat-spinner diameter="40"></mat-spinner>
        </div>
      }
    
      <!-- Incidents list -->
      @if (!loading) {
        <div class="incidents-list">
          @for (incident of filteredIncidents; track incident) {
            <mat-card class="incident-card"
              [class]="'severity-border-' + incident.severity">
              <mat-card-content>
                <div class="incident-header">
                  <div class="incident-info">
                    <div class="incident-title-row">
                      <h3>{{ incident.title }}</h3>
                    </div>
                    <div class="incident-badges">
                      <span class="severity-chip" [class]="'severity-' + incident.severity">
                        {{ incident.severity }}
                      </span>
                      <span class="status-chip" [class]="'status-' + incident.status">
                        <mat-icon class="chip-icon">{{ getStatusIcon(incident.status) }}</mat-icon>
                        {{ incident.status }}
                      </span>
                      @if (incident.affectedService) {
                        <span class="service-chip">
                          <mat-icon class="chip-icon">dns</mat-icon>
                          {{ incident.affectedService }}
                        </span>
                      }
                    </div>
                  </div>
                </div>
                <p class="incident-description">{{ incident.description }}</p>
                <!-- Devin Session Status -->
                @if (incident.devinSessionId) {
                  <div class="devin-session">
                    <div class="devin-header">
                      <mat-icon class="devin-icon">smart_toy</mat-icon>
                      <span class="devin-label">Devin Session</span>
                      <span class="devin-status" [class]="'devin-' + (incident.devinSessionStatus || 'unknown')">
                        {{ incident.devinSessionStatus || 'pending' }}
                      </span>
                    </div>
                    <div class="devin-details">
                      <span class="session-id">{{ incident.devinSessionId }}</span>
                      @if (incident.devinSessionUrl) {
                        <a [href]="incident.devinSessionUrl"
                          target="_blank" rel="noopener" class="session-link">
                          <mat-icon>open_in_new</mat-icon>
                          View Session
                        </a>
                      }
                    </div>
                  </div>
                }
                @if (!incident.devinSessionId && incident.active) {
                  <div class="devin-session devin-pending">
                    <mat-icon class="devin-icon">smart_toy</mat-icon>
                    <span>No Devin session</span>
                    <button mat-stroked-button color="primary" (click)="triggerSession(incident)"
                      [disabled]="incident.devinSessionStatus === 'triggering'" class="trigger-btn">
                      <mat-icon>play_arrow</mat-icon>
                      {{ incident.devinSessionStatus === 'triggering' ? 'Launching...' : 'Launch Devin' }}
                    </button>
                  </div>
                }
                <div class="incident-actions">
                  @if (incident.status === 'open' || incident.status === 'investigating') {
                    <button mat-stroked-button color="primary"
                      (click)="resolveIncident(incident)">
                      <mat-icon>check_circle</mat-icon> Resolve
                    </button>
                  }
                  @if (incident.status === 'resolved') {
                    <button mat-stroked-button
                      (click)="closeIncident(incident)">
                      <mat-icon>cancel</mat-icon> Close
                    </button>
                  }
                  <button mat-stroked-button color="warn" (click)="deleteIncident(incident)">
                    <mat-icon>delete</mat-icon> Delete
                  </button>
                </div>
                <div class="incident-meta">
                  <span>Created {{ incident.createdAt | date:'medium' }}</span>
                  @if (incident.resolvedAt) {
                    <span>Resolved {{ incident.resolvedAt | date:'medium' }}</span>
                  }
                  @if (incident.closedAt) {
                    <span>Closed {{ incident.closedAt | date:'medium' }}</span>
                  }
                </div>
              </mat-card-content>
            </mat-card>
          }
        </div>
      }
    
      <!-- Empty state -->
      @if (!loading && filteredIncidents.length === 0 && filterStatus) {
        <div class="empty-state">
          <mat-icon>filter_list_off</mat-icon>
          <p>No {{ filterStatus }} incidents</p>
          <button mat-stroked-button (click)="filterStatus = ''">Clear Filter</button>
        </div>
      }
    
      <!-- First-run onboarding (no incidents at all) -->
      @if (!loading && incidents.length === 0 && !filterStatus) {
        <div class="onboarding">
          <mat-card class="onboarding-card">
            <mat-card-content>
              <div class="onboarding-hero">
                <mat-icon class="onboarding-icon">smart_toy</mat-icon>
                <h2>Automated Incident Response</h2>
                <p>Report an incident and Devin will automatically spin up a session to investigate the root cause across OtterWorks' 11 microservices.</p>
              </div>
              <div class="onboarding-steps">
                <div class="step">
                  <div class="step-number">1</div>
                  <div><strong>Report an incident</strong><br>Describe the issue, select severity and affected service</div>
                </div>
                <div class="step">
                  <div class="step-number">2</div>
                  <div><strong>Devin session launches</strong><br>An AI session is created with full architecture context</div>
                </div>
                <div class="step">
                  <div class="step-number">3</div>
                  <div><strong>Track progress</strong><br>Monitor the session status and view Devin's investigation live</div>
                </div>
              </div>
              <button mat-raised-button color="warn" (click)="showCreateForm = true" class="onboarding-cta">
                <mat-icon>add_alert</mat-icon>
                Report Your First Incident
              </button>
            </mat-card-content>
          </mat-card>
        </div>
      }
    </div>
    `,
    styles: [`
    .page-container{padding:0}.page-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px}
    .page-title{font-size:1.5rem;font-weight:600;color:#333;margin:0}.page-subtitle{font-size:.85rem;color:#777;margin:4px 0 0}
    .loading-container{display:flex;justify-content:center;padding:60px}
    .status-summary{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}
    .summary-chip{display:flex;align-items:center;gap:6px;padding:8px 16px;border-radius:8px;font-size:.85rem;cursor:pointer}
    .summary-chip.active{background:#fff3e0;color:#e65100}.summary-chip.investigating{background:#e3f2fd;color:#1565c0}.summary-chip.resolved{background:#e8f5e9;color:#2e7d32}.summary-chip.closed{background:#eceff1;color:#546e7a}
    .create-form{margin-bottom:24px;border-left:4px solid #f44336}.create-form mat-card-title{display:flex;align-items:center;gap:8px}
    .full-width{width:100%}.form-row{display:flex;gap:16px}
    .incidents-list{display:flex;flex-direction:column;gap:16px}
    .incident-card{border-left:4px solid transparent}.severity-border-low{border-left-color:#4caf50}.severity-border-medium{border-left-color:#2196f3}.severity-border-high{border-left-color:#ff9800}.severity-border-critical{border-left-color:#f44336}
    .incident-header{display:flex;justify-content:space-between;align-items:flex-start}
    .incident-title-row h3{margin:0 0 8px;font-size:1.05rem}.incident-badges{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .severity-chip,.status-chip,.service-chip{display:flex;align-items:center;gap:3px;padding:3px 8px;border-radius:4px;font-size:.7rem;font-weight:600;text-transform:uppercase}
    .chip-icon{font-size:13px;width:13px;height:13px}
    .severity-low{background:#e8f5e9;color:#2e7d32}.severity-medium{background:#e3f2fd;color:#1565c0}.severity-high{background:#fff3e0;color:#e65100}.severity-critical{background:#ffebee;color:#c62828}
    .status-open{background:#fff3e0;color:#e65100}.status-investigating{background:#e3f2fd;color:#1565c0}.status-resolved{background:#e8f5e9;color:#2e7d32}.status-closed{background:#eceff1;color:#546e7a}
    .service-chip{background:#f3e5f5;color:#7b1fa2}
    .incident-description{color:#555;line-height:1.6;margin:16px 0;font-size:.9rem}
    .devin-session{background:#f8f9ff;border:1px solid #e0e7ff;border-radius:8px;padding:12px 16px;margin:12px 0}
    .devin-header{display:flex;align-items:center;gap:8px;margin-bottom:6px}
    .devin-icon{color:#1565c0}.devin-label{font-weight:600;font-size:.85rem;color:#333}
    .devin-status{padding:2px 8px;border-radius:12px;font-size:.7rem;font-weight:600;text-transform:uppercase}
    .devin-running{background:#e3f2fd;color:#1565c0}.devin-stopped{background:#e8f5e9;color:#2e7d32}.devin-failed{background:#ffebee;color:#c62828}.devin-unknown{background:#eceff1;color:#546e7a}.devin-blocked{background:#fff3e0;color:#e65100}
    .devin-details{display:flex;align-items:center;gap:16px;font-size:.8rem}
    .session-id{color:#999;font-family:monospace;font-size:.75rem}
    .session-link{display:flex;align-items:center;gap:4px;color:#1565c0;text-decoration:none;font-weight:500}
    .session-link:hover{text-decoration:underline}.session-link .mat-icon{font-size:16px;width:16px;height:16px}
    .devin-pending{display:flex;align-items:center;gap:8px;color:#999;font-size:.85rem;background:#fafafa;border-color:#eee}.trigger-btn{margin-left:auto}
    .incident-actions{display:flex;gap:8px;margin:12px 0;flex-wrap:wrap}
    .incident-actions button{font-size:.8rem}
    .incident-actions .mat-icon{font-size:16px;width:16px;height:16px;margin-right:2px}
    .incident-meta{display:flex;gap:16px;font-size:.8rem;color:#999;flex-wrap:wrap;margin-top:8px}
    .empty-state{display:flex;flex-direction:column;align-items:center;padding:60px;color:#999}.empty-state .mat-icon{font-size:48px;width:48px;height:48px;margin-bottom:12px}
    .onboarding-card{max-width:640px;margin:0 auto}.onboarding-hero{text-align:center;margin-bottom:24px}
    .onboarding-icon{font-size:56px;width:56px;height:56px;color:#1565c0}.onboarding-hero h2{margin:0 0 8px;font-size:1.3rem}.onboarding-hero p{color:#666;line-height:1.5;margin:0}
    .onboarding-steps{display:flex;flex-direction:column;gap:16px;margin-bottom:24px}.step{display:flex;gap:12px}.step-number{min-width:28px;height:28px;border-radius:50%;background:#1565c0;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:.85rem}
    .onboarding-cta{margin:0 auto}
    .demo-controls{margin-bottom:24px;border-left:4px solid #7b1fa2;background:#fdf8ff}
    .demo-controls mat-card-title{display:flex;align-items:center;gap:8px;font-size:1rem}
    .demo-badge{background:#7b1fa2;color:#fff;border-radius:12px;padding:2px 8px;font-size:.7rem;font-weight:600}
    .chaos-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:16px}
    .chaos-scenario{background:#fff;border:1px solid #e8d5f5;border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:12px;transition:border-color .2s}
    .chaos-scenario.chaos-active{border-color:#7b1fa2;background:#fdf0ff}
    .chaos-scenario .mat-icon{flex-shrink:0;overflow:visible}
    .chaos-scenario-header{display:flex;gap:12px;align-items:flex-start}
    .chaos-svc-icon{color:#7b1fa2;margin-top:2px}
    .chaos-svc-name{font-weight:600;font-size:.9rem;color:#333}
    .chaos-lang{background:#f3e5f5;color:#7b1fa2;border-radius:4px;padding:1px 6px;font-size:.7rem;font-weight:500;margin-left:6px}
    .chaos-svc-desc{font-size:.8rem;color:#777;line-height:1.4;margin-top:4px}
    .chaos-status{display:flex;align-items:center;gap:6px;font-size:.8rem;color:#7b1fa2;font-weight:600;background:#f3e5f5;border-radius:6px;padding:6px 10px}
    .chaos-active-icon{font-size:16px;width:16px;height:16px}
    .chaos-footer{display:flex;align-items:center;justify-content:space-between;margin-top:16px;padding-top:16px;border-top:1px solid #e8d5f5}
    .chaos-note{font-size:.8rem;color:#999}
    .auto-investigate-toggle{display:flex;align-items:center;justify-content:space-between;padding:16px;background:#e8f0fe;border:1px solid #c2d7f9;border-radius:8px;margin-bottom:16px}
    .toggle-info{flex:1}.toggle-label{display:flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;color:#1565c0;margin-bottom:4px}
    .toggle-description{font-size:.8rem;color:#555;line-height:1.4}
  `]
})
export class IncidentsComponent implements OnInit, OnDestroy {
  incidents: Incident[] = [];
  loading = true;
  creating = false;
  showCreateForm = false;
  showClosed = false;
  filterStatus = '';
  affectedServices = AFFECTED_SERVICES;
  newIncident: Partial<Incident> = { severity: 'high', affectedService: '' };

  // Demo Controls state
  showDemoControls = true;
  chaosLoading = false;
  chaosState: Record<string, boolean> = {};

  // Auto-investigate toggle
  autoInvestigate = true;
  autoInvestigateLoading = false;

  get activeChaosCount(): number {
    return Object.values(this.chaosState).filter(Boolean).length;
  }

  private pollSub?: Subscription;

  constructor(
    private api: AdminApiService,
    private snackBar: MatSnackBar,
    private dialog: MatDialog,
  ) {}

  ngOnInit(): void {
    this.loadChaosState();
    this.api.getAutoInvestigate().subscribe({
      next: (res) => this.autoInvestigate = res.enabled,
      error: () => this.autoInvestigate = true,
    });
    this.loadIncidents();
    // Poll for status updates every 10 seconds
    this.pollSub = interval(10000).subscribe(() => {
      if (this.incidents.some(i => i.active && i.devinSessionId)) {
        this.loadIncidents();
      }
    });
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }

  get activeCount(): number {
    return this.incidents.filter(i => i.active).length;
  }

  get investigatingCount(): number {
    return this.incidents.filter(i => i.status === 'investigating' && i.devinSessionId).length;
  }

  get resolvedCount(): number {
    return this.incidents.filter(i => i.status === 'resolved').length;
  }

  get closedCount(): number {
    return this.incidents.filter(i => i.status === 'closed').length;
  }

  get filteredIncidents(): Incident[] {
    let result = this.incidents;

    if (!this.showClosed) {
      result = result.filter(i => i.status !== 'closed');
    }

    if (!this.filterStatus) return result;
    if (this.filterStatus === 'active') return result.filter(i => i.active);
    if (this.filterStatus === 'investigating') return result.filter(i => i.status === 'investigating');
    return result.filter(i => i.status === this.filterStatus);
  }

  loadChaosState(): void {
    const stored = localStorage.getItem(CHAOS_STATE_KEY);
    if (stored) {
      try {
        this.chaosState = JSON.parse(stored);
      } catch {
        this.chaosState = {};
      }
    }
  }

  saveChaosState(): void {
    localStorage.setItem(CHAOS_STATE_KEY, JSON.stringify(this.chaosState));
  }

  loadIncidents(): void {
    this.api.getIncidents().subscribe({
      next: (incidents) => {
        this.incidents = incidents;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.snackBar.open('Failed to load incidents', 'Dismiss', { duration: 3000 });
      },
    });
  }

  createIncident(): void {
    if (!this.newIncident.title || !this.newIncident.description) return;

    this.creating = true;
    this.api.createIncident(this.newIncident).subscribe({
      next: (incident) => {
        this.incidents.unshift(incident);
        this.showCreateForm = false;
        this.creating = false;
        this.newIncident = { severity: 'high', affectedService: '' };

        const sessionMsg = incident.devinSessionId
          ? ` Devin session launched.`
          : ' (Devin session pending)';
        this.snackBar.open('Incident created!' + sessionMsg, 'Dismiss', { duration: 5000 });
      },
      error: () => {
        this.creating = false;
        this.snackBar.open('Failed to create incident', 'Dismiss', { duration: 3000 });
      },
    });
  }

  triggerSession(incident: Incident): void {
    incident.devinSessionStatus = 'triggering';
    this.api.triggerDevinSession(incident.id).subscribe({
      next: (updated) => {
        const idx = this.incidents.findIndex(i => i.id === incident.id);
        if (idx !== -1) this.incidents[idx] = updated;
        this.snackBar.open('Devin session launched!', 'Dismiss', { duration: 5000 });
      },
      error: () => {
        incident.devinSessionStatus = null;
        this.snackBar.open('Failed to launch Devin session', 'Dismiss', { duration: 3000 });
      },
    });
  }

  resolveIncident(incident: Incident): void {
    const ref = this.dialog.open(ConfirmDialogComponent, {
      data: {
        title: 'Resolve Incident',
        message: `Mark "${incident.title}" as resolved?`,
        confirmText: 'Resolve',
        confirmColor: 'primary',
      },
    });
    ref.afterClosed().subscribe(confirmed => {
      if (!confirmed) return;
      this.api.updateIncidentStatus(incident.id, 'resolved').subscribe({
        next: (updated) => {
          const idx = this.incidents.findIndex(i => i.id === incident.id);
          if (idx !== -1) this.incidents[idx] = updated;
          this.snackBar.open('Incident resolved', 'Dismiss', { duration: 3000 });
        },
        error: (err) => {
          const detail = err.error?.details || 'Failed to resolve incident';
          this.snackBar.open(detail, 'Dismiss', { duration: 4000 });
        },
      });
    });
  }

  closeIncident(incident: Incident): void {
    const ref = this.dialog.open(ConfirmDialogComponent, {
      data: {
        title: 'Close Incident',
        message: `Close "${incident.title}"? Closed incidents are hidden from the default view.`,
        confirmText: 'Close',
        confirmColor: 'accent',
      },
    });
    ref.afterClosed().subscribe(confirmed => {
      if (!confirmed) return;
      this.api.updateIncidentStatus(incident.id, 'closed').subscribe({
        next: (updated) => {
          const idx = this.incidents.findIndex(i => i.id === incident.id);
          if (idx !== -1) this.incidents[idx] = updated;
          this.snackBar.open('Incident closed', 'Dismiss', { duration: 3000 });
        },
        error: (err) => {
          const detail = err.error?.details || 'Failed to close incident';
          this.snackBar.open(detail, 'Dismiss', { duration: 4000 });
        },
      });
    });
  }

  deleteIncident(incident: Incident): void {
    const ref = this.dialog.open(ConfirmDialogComponent, {
      data: {
        title: 'Delete Incident',
        message: `Permanently delete "${incident.title}"? This cannot be undone.`,
        confirmText: 'Delete',
        confirmColor: 'warn',
      },
    });
    ref.afterClosed().subscribe(confirmed => {
      if (!confirmed) return;
      this.api.deleteIncident(incident.id).subscribe({
        next: () => {
          this.incidents = this.incidents.filter(i => i.id !== incident.id);
          this.snackBar.open('Incident deleted', 'Dismiss', { duration: 3000 });
        },
        error: (err) => {
          const detail = err.error?.details || err.error?.error || 'Failed to delete incident';
          this.snackBar.open(detail, 'Dismiss', { duration: 4000 });
        },
      });
    });
  }

  getStatusIcon(status: string): string {
    switch (status) {
      case 'open': return 'error_outline';
      case 'investigating': return 'smart_toy';
      case 'resolved': return 'check_circle';
      case 'closed': return 'cancel';
      default: return 'help_outline';
    }
  }

  triggerChaos(service: string, scenario: string): void {
    this.chaosLoading = true;
    this.api.triggerChaos(service, scenario).subscribe({
      next: () => {
        this.chaosState[service] = true;
        this.saveChaosState();
        this.chaosLoading = false;
        this.snackBar.open(
          `Chaos active on ${service} — watch Grafana for the alert (auto-resets in 10m)`,
          'Dismiss',
          { duration: 6000 },
        );
      },
      error: () => {
        this.chaosLoading = false;
        this.snackBar.open(`Failed to trigger chaos on ${service}`, 'Dismiss', { duration: 3000 });
      },
    });
  }

  toggleAutoInvestigate(enabled: boolean): void {
    const previous = this.autoInvestigate;
    this.autoInvestigate = enabled;
    this.autoInvestigateLoading = true;
    this.api.setAutoInvestigate(enabled).subscribe({
      next: (res) => {
        this.autoInvestigate = res.enabled;
        this.autoInvestigateLoading = false;
        const mode = res.enabled
          ? 'ON — Devin sessions will be auto-created from alerts'
          : 'OFF — Incidents created from alerts, but no auto Devin sessions';
        this.snackBar.open(`Auto-Investigate: ${mode}`, 'Dismiss', { duration: 5000 });
      },
      error: () => {
        this.autoInvestigate = previous;
        this.autoInvestigateLoading = false;
        this.snackBar.open('Failed to update auto-investigate setting', 'Dismiss', { duration: 3000 });
      },
    });
  }

  resetAllChaos(): void {
    this.chaosLoading = true;
    this.api.resetChaos().subscribe({
      next: (res) => {
        this.chaosState = {};
        this.saveChaosState();
        this.chaosLoading = false;
        const cleared = res.cleared?.length ?? 0;
        const resolved = (res as any).resolved_incidents?.length ?? 0;
        const msg = resolved > 0
          ? `Reset complete — cleared ${cleared} chaos flag(s), resolved ${resolved} incident(s)`
          : `Reset complete — cleared ${cleared} chaos flag(s)`;
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
        this.loadIncidents();
      },
      error: () => {
        this.chaosLoading = false;
        this.snackBar.open('Failed to reset chaos flags', 'Dismiss', { duration: 3000 });
      },
    });
  }
}
