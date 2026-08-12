import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { format } from "date-fns";
import {
  AFFECTED_SERVICES,
  Incident,
  IncidentSeverity,
  IncidentStatus,
  NewIncident,
  apiErrorDetail,
  createIncident,
  deleteIncident,
  getAutoInvestigate,
  getIncidents,
  resetChaos,
  setAutoInvestigate,
  triggerChaos,
  triggerDevinSession,
  updateIncidentStatus,
} from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Spinner } from "@/components/Spinner";

const CHAOS_STATE_KEY = "ow_admin_chaos_state";
const INCIDENTS_QUERY_KEY = ["incidents"];

// Matches Angular's `date:'medium'` pipe output (e.g. "Aug 12, 2026, 7:00:00 AM")
function formatMedium(value: string): string {
  try {
    return format(new Date(value), "MMM d, y, h:mm:ss a");
  } catch {
    return value;
  }
}

function statusIcon(status: string): string {
  switch (status) {
    case "open":
      return "error_outline";
    case "investigating":
      return "smart_toy";
    case "resolved":
      return "check_circle";
    case "closed":
      return "cancel";
    default:
      return "help_outline";
  }
}

function loadChaosState(): Record<string, boolean> {
  const stored = localStorage.getItem(CHAOS_STATE_KEY);
  if (!stored) return {};
  try {
    return JSON.parse(stored) as Record<string, boolean>;
  } catch {
    return {};
  }
}

interface ChaosScenario {
  service: string;
  scenario: string;
  icon: string;
  name: string;
  lang: string;
  desc: string;
  alertNote: string;
  idleLabel: string;
  activeLabel: string;
}

const CHAOS_SCENARIOS: ChaosScenario[] = [
  {
    service: "search-service",
    scenario: "suggest_500",
    icon: "search",
    name: "Search Service",
    lang: "Python/Flask",
    desc: "Autocomplete suggest endpoint crashes with KeyError on ranking score enrichment → 500s",
    alertNote: "Chaos active — Grafana alert fires in ~30s",
    idleLabel: "Break Search Autocomplete",
    activeLabel: "Breaking...",
  },
  {
    service: "file-service",
    scenario: "upload_s3_error",
    icon: "cloud_upload",
    name: "File Service",
    lang: "Rust/Actix-Web",
    desc: "Uploads routed to nonexistent S3 bucket → AWS NoSuchBucket errors → 500s",
    alertNote: "Chaos active — Grafana alert fires in ~30s",
    idleLabel: "Break File Uploads",
    activeLabel: "Breaking...",
  },
  {
    service: "notification-service",
    scenario: "consumer_strict_schema",
    icon: "notifications",
    name: "Notification Service",
    lang: "Kotlin/Ktor",
    desc: "Strict JSON schema rejects legacy message timestamps → deserialization loop → queue backlog grows",
    alertNote: "Chaos active — Grafana alert fires in ~2m",
    idleLabel: "Break Notification Queue",
    activeLabel: "Breaking...",
  },
  {
    service: "document-service",
    scenario: "slow_queries",
    icon: "description",
    name: "Document Service",
    lang: "Python/FastAPI",
    desc: "Injected 3-5s latency before every database query → P95 latency spike → timeout cascade",
    alertNote: "Chaos active — Grafana alert fires in ~1m",
    idleLabel: "Inject Query Latency",
    activeLabel: "Slowing...",
  },
];

interface ConfirmState {
  title: string;
  message: string;
  confirmText: string;
  confirmColor: "primary" | "accent" | "warn";
  onConfirm: () => void;
}

const EMPTY_FORM: NewIncident = {
  title: "",
  description: "",
  severity: "high",
  affectedService: "",
};

export function IncidentsPage() {
  const queryClient = useQueryClient();

  const [showCreateForm, setShowCreateForm] = useState(false);
  const [showClosed, setShowClosed] = useState(false);
  const [filterStatus, setFilterStatus] = useState("");
  const [newIncident, setNewIncident] = useState<NewIncident>(EMPTY_FORM);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [triggeringIds, setTriggeringIds] = useState<Set<string>>(new Set());

  // Demo Controls state
  const [showDemoControls, setShowDemoControls] = useState(true);
  const [chaosState, setChaosState] = useState<Record<string, boolean>>(loadChaosState);
  const [autoInvestigate, setAutoInvestigateState] = useState(true);

  const activeChaosCount = Object.values(chaosState).filter(Boolean).length;

  const saveChaosState = (state: Record<string, boolean>) => {
    setChaosState(state);
    localStorage.setItem(CHAOS_STATE_KEY, JSON.stringify(state));
  };

  const incidentsQuery = useQuery({
    queryKey: INCIDENTS_QUERY_KEY,
    queryFn: getIncidents,
    // Poll for status updates every 10 seconds while a Devin session is active
    refetchInterval: (query) =>
      (query.state.data ?? []).some((i) => i.active && i.devinSessionId) ? 10000 : false,
  });
  const incidents = useMemo(() => incidentsQuery.data ?? [], [incidentsQuery.data]);
  const loading = incidentsQuery.isPending;

  useEffect(() => {
    if (incidentsQuery.isError) {
      toast.error("Failed to load incidents");
    }
  }, [incidentsQuery.isError]);

  useEffect(() => {
    getAutoInvestigate()
      .then((res) => setAutoInvestigateState(res.enabled))
      .catch(() => setAutoInvestigateState(true));
  }, []);

  const setIncidents = (updater: (prev: Incident[]) => Incident[]) => {
    queryClient.setQueryData<Incident[]>(INCIDENTS_QUERY_KEY, (prev) => updater(prev ?? []));
  };

  const createMutation = useMutation({
    mutationFn: createIncident,
    onSuccess: (incident) => {
      setIncidents((prev) => [incident, ...prev]);
      setShowCreateForm(false);
      setNewIncident(EMPTY_FORM);
      const sessionMsg = incident.devinSessionId
        ? " Devin session launched."
        : " (Devin session pending)";
      toast.success("Incident created!" + sessionMsg, { duration: 5000 });
    },
    onError: () => toast.error("Failed to create incident"),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: IncidentStatus }) =>
      updateIncidentStatus(id, status),
    onSuccess: (updated, { status }) => {
      setIncidents((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      toast.success(status === "resolved" ? "Incident resolved" : "Incident closed");
    },
    onError: (err, { status }) => {
      const fallback =
        status === "resolved" ? "Failed to resolve incident" : "Failed to close incident";
      toast.error(apiErrorDetail(err, fallback), { duration: 4000 });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteIncident,
    onSuccess: (_data, id) => {
      setIncidents((prev) => prev.filter((i) => i.id !== id));
      toast.success("Incident deleted");
    },
    onError: (err) =>
      toast.error(apiErrorDetail(err, "Failed to delete incident"), { duration: 4000 }),
  });

  const triggerSessionMutation = useMutation({
    mutationFn: triggerDevinSession,
    onSuccess: (updated) => {
      setIncidents((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      toast.success("Devin session launched!", { duration: 5000 });
    },
    onError: () => toast.error("Failed to launch Devin session"),
    onSettled: (_data, _err, id) => {
      setTriggeringIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    },
  });

  const chaosMutation = useMutation({
    mutationFn: ({ service, scenario }: { service: string; scenario: string }) =>
      triggerChaos(service, scenario),
    onSuccess: (_res, { service }) => {
      saveChaosState({ ...chaosState, [service]: true });
      toast.success(
        `Chaos active on ${service} — watch Grafana for the alert (auto-resets in 10m)`,
        { duration: 6000 }
      );
    },
    onError: (_err, { service }) => toast.error(`Failed to trigger chaos on ${service}`),
  });

  const resetChaosMutation = useMutation({
    mutationFn: resetChaos,
    onSuccess: (res) => {
      saveChaosState({});
      const cleared = res.cleared?.length ?? 0;
      const resolved = res.resolvedIncidents?.length ?? 0;
      const msg =
        resolved > 0
          ? `Reset complete — cleared ${cleared} chaos flag(s), resolved ${resolved} incident(s)`
          : `Reset complete — cleared ${cleared} chaos flag(s)`;
      toast.success(msg, { duration: 5000 });
      queryClient.invalidateQueries({ queryKey: INCIDENTS_QUERY_KEY });
    },
    onError: () => toast.error("Failed to reset chaos flags"),
  });

  const autoInvestigateMutation = useMutation({
    mutationFn: setAutoInvestigate,
    onMutate: (enabled: boolean) => {
      const previous = autoInvestigate;
      setAutoInvestigateState(enabled);
      return { previous };
    },
    onSuccess: (res) => {
      setAutoInvestigateState(res.enabled);
      const mode = res.enabled
        ? "ON — Devin sessions will be auto-created from alerts"
        : "OFF — Incidents created from alerts, but no auto Devin sessions";
      toast.success(`Auto-Investigate: ${mode}`, { duration: 5000 });
    },
    onError: (_err, _enabled, context) => {
      setAutoInvestigateState(context?.previous ?? true);
      toast.error("Failed to update auto-investigate setting");
    },
  });

  const chaosLoading = chaosMutation.isPending || resetChaosMutation.isPending;

  const activeCount = incidents.filter((i) => i.active).length;
  const investigatingCount = incidents.filter(
    (i) => i.status === "investigating" && i.devinSessionId
  ).length;
  const resolvedCount = incidents.filter((i) => i.status === "resolved").length;
  const closedCount = incidents.filter((i) => i.status === "closed").length;

  const filteredIncidents = useMemo(() => {
    let result = incidents;
    if (!showClosed) {
      result = result.filter((i) => i.status !== "closed");
    }
    if (!filterStatus) return result;
    if (filterStatus === "active") return result.filter((i) => i.active);
    if (filterStatus === "investigating")
      return result.filter((i) => i.status === "investigating");
    return result.filter((i) => i.status === filterStatus);
  }, [incidents, showClosed, filterStatus]);

  const onCreate = () => {
    if (!newIncident.title || !newIncident.description) return;
    createMutation.mutate(newIncident);
  };

  const onTriggerSession = (incident: Incident) => {
    setTriggeringIds((prev) => new Set(prev).add(incident.id));
    triggerSessionMutation.mutate(incident.id);
  };

  const onResolve = (incident: Incident) =>
    setConfirm({
      title: "Resolve Incident",
      message: `Mark "${incident.title}" as resolved?`,
      confirmText: "Resolve",
      confirmColor: "primary",
      onConfirm: () => statusMutation.mutate({ id: incident.id, status: "resolved" }),
    });

  const onClose = (incident: Incident) =>
    setConfirm({
      title: "Close Incident",
      message: `Close "${incident.title}"? Closed incidents are hidden from the default view.`,
      confirmText: "Close",
      confirmColor: "accent",
      onConfirm: () => statusMutation.mutate({ id: incident.id, status: "closed" }),
    });

  const onDelete = (incident: Incident) =>
    setConfirm({
      title: "Delete Incident",
      message: `Permanently delete "${incident.title}"? This cannot be undone.`,
      confirmText: "Delete",
      confirmColor: "warn",
      onConfirm: () => deleteMutation.mutate(incident.id),
    });

  return (
    <div className="page-container incidents-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Incident Response</h1>
          <p className="page-subtitle">
            Create incident tickets to trigger automated Devin investigation sessions
          </p>
        </div>
        <button
          type="button"
          className="btn btn-raised btn-warn"
          onClick={() => setShowCreateForm((v) => !v)}
        >
          <span className="material-icons" aria-hidden>
            {showCreateForm ? "close" : "add_alert"}
          </span>
          {showCreateForm ? "Cancel" : "Report Incident"}
        </button>
      </div>

      {/* Demo Controls: chaos injection panel for workshop use */}
      <section className="card demo-controls">
        <header
          className="demo-controls-header"
          onClick={() => setShowDemoControls((v) => !v)}
        >
          <h2 className="card-title">
            <span className="material-icons" aria-hidden>
              science
            </span>
            Demo Controls
            {activeChaosCount > 0 && <span className="demo-badge">{activeChaosCount} active</span>}
          </h2>
          <p className="card-subtitle">
            Inject realistic failures and control the investigation flow: manual, one-click, or
            fully automatic
          </p>
        </header>
        {showDemoControls && (
          <div className="demo-controls-body">
            <div className="auto-investigate-toggle">
              <div className="toggle-info">
                <div className="toggle-label">
                  <span className="material-icons" aria-hidden>
                    smart_toy
                  </span>
                  <span id="auto-investigate-label">Auto-Investigate with Devin</span>
                </div>
                <div className="toggle-description">
                  {autoInvestigate ? (
                    <>
                      <strong>ON:</strong> Grafana alerts auto-create incidents AND launch Devin
                      sessions (Flow 3: fully automatic)
                    </>
                  ) : (
                    <>
                      <strong>OFF:</strong> Grafana alerts auto-create incidents but do NOT launch
                      Devin. Use "Launch Devin" button on individual incidents (Flow 2) or
                      investigate manually via Grafana (Flow 1)
                    </>
                  )}
                </div>
              </div>
              <input
                type="checkbox"
                role="switch"
                className="slide-toggle"
                aria-labelledby="auto-investigate-label"
                checked={autoInvestigate}
                disabled={autoInvestigateMutation.isPending}
                onChange={(e) => autoInvestigateMutation.mutate(e.target.checked)}
              />
            </div>
            <div className="chaos-grid">
              {CHAOS_SCENARIOS.map((s) => {
                const active = !!chaosState[s.service];
                return (
                  <div key={s.service} className={`chaos-scenario${active ? " chaos-active" : ""}`}>
                    <div className="chaos-scenario-header">
                      <span className="material-icons chaos-svc-icon" aria-hidden>
                        {s.icon}
                      </span>
                      <div>
                        <div className="chaos-svc-name">
                          {s.name} <span className="chaos-lang">{s.lang}</span>
                        </div>
                        <div className="chaos-svc-desc">{s.desc}</div>
                      </div>
                    </div>
                    {active && (
                      <div className="chaos-status">
                        <span className="material-icons chaos-active-icon" aria-hidden>
                          bolt
                        </span>
                        {s.alertNote}
                      </div>
                    )}
                    <button
                      type="button"
                      className="btn btn-raised btn-warn"
                      disabled={active || chaosLoading}
                      onClick={() =>
                        chaosMutation.mutate({ service: s.service, scenario: s.scenario })
                      }
                    >
                      <span className="material-icons" aria-hidden>
                        {active ? "check" : "bug_report"}
                      </span>
                      {active ? s.activeLabel : s.idleLabel}
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="chaos-footer">
              <span className="chaos-note">Chaos flags auto-expire after 10 minutes</span>
              <button
                type="button"
                className="btn btn-stroked btn-warn"
                disabled={activeChaosCount === 0 || chaosLoading}
                onClick={() => resetChaosMutation.mutate()}
              >
                <span className="material-icons" aria-hidden>
                  refresh
                </span>
                Reset All
              </button>
            </div>
          </div>
        )}
      </section>

      {/* Status summary chips (only show when there are incidents) */}
      {!loading && incidents.length > 0 && (
        <div className="status-summary">
          <div
            className="summary-chip active"
            onClick={() => setFilterStatus((f) => (f === "active" ? "" : "active"))}
          >
            <span className="material-icons" aria-hidden>
              warning
            </span>
            <span>{activeCount} Active</span>
          </div>
          <div
            className="summary-chip investigating"
            onClick={() => setFilterStatus((f) => (f === "investigating" ? "" : "investigating"))}
          >
            <span className="material-icons" aria-hidden>
              smart_toy
            </span>
            <span>{investigatingCount} Devin Investigating</span>
          </div>
          <div
            className="summary-chip resolved"
            onClick={() => setFilterStatus((f) => (f === "resolved" ? "" : "resolved"))}
          >
            <span className="material-icons" aria-hidden>
              check_circle
            </span>
            <span>{resolvedCount} Resolved</span>
          </div>
          {closedCount > 0 && (
            <div className="summary-chip closed" onClick={() => setShowClosed((v) => !v)}>
              <span className="material-icons" aria-hidden>
                {showClosed ? "visibility" : "visibility_off"}
              </span>
              <span>
                {closedCount} Closed{showClosed ? "" : " (hidden)"}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Create form */}
      {showCreateForm && (
        <section className="card create-form">
          <h2 className="card-title">
            <span className="material-icons" aria-hidden>
              add_alert
            </span>
            Report New Incident
          </h2>
          <p className="card-subtitle">
            A Devin session will automatically be created to investigate this incident
          </p>
          <div className="form-field full-width">
            <label htmlFor="incident-title">Incident Title</label>
            <input
              id="incident-title"
              type="text"
              value={newIncident.title}
              placeholder="Brief description of the issue"
              onChange={(e) => setNewIncident((v) => ({ ...v, title: e.target.value }))}
            />
          </div>
          <div className="form-field full-width">
            <label htmlFor="incident-description">Description</label>
            <textarea
              id="incident-description"
              rows={4}
              value={newIncident.description}
              placeholder="Detailed description: what's happening, error messages, affected users..."
              onChange={(e) => setNewIncident((v) => ({ ...v, description: e.target.value }))}
            />
          </div>
          <div className="form-row">
            <div className="form-field">
              <label htmlFor="incident-severity">Severity</label>
              <select
                id="incident-severity"
                value={newIncident.severity}
                onChange={(e) =>
                  setNewIncident((v) => ({
                    ...v,
                    severity: e.target.value as IncidentSeverity,
                  }))
                }
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
            </div>
            <div className="form-field">
              <label htmlFor="incident-service">Affected Service</label>
              <select
                id="incident-service"
                value={newIncident.affectedService}
                onChange={(e) =>
                  setNewIncident((v) => ({ ...v, affectedService: e.target.value }))
                }
              >
                <option value=""></option>
                {AFFECTED_SERVICES.map((svc) => (
                  <option key={svc.value} value={svc.value}>
                    {svc.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <button
            type="button"
            className="btn btn-raised btn-warn"
            disabled={!newIncident.title || !newIncident.description || createMutation.isPending}
            onClick={onCreate}
          >
            <span className="material-icons" aria-hidden>
              {createMutation.isPending ? "hourglass_empty" : "smart_toy"}
            </span>
            {createMutation.isPending
              ? "Creating Devin Session..."
              : "Create Incident & Launch Devin"}
          </button>
        </section>
      )}

      {/* Loading */}
      {loading && <Spinner />}

      {/* Incidents list */}
      {!loading && (
        <div className="incidents-list">
          {filteredIncidents.map((incident) => (
            <article
              key={incident.id}
              className={`card incident-card severity-border-${incident.severity}`}
            >
              <div className="incident-header">
                <div className="incident-info">
                  <div className="incident-title-row">
                    <h3>{incident.title}</h3>
                  </div>
                  <div className="incident-badges">
                    <span className={`severity-chip severity-${incident.severity}`}>
                      {incident.severity}
                    </span>
                    <span className={`status-chip status-${incident.status}`}>
                      <span className="material-icons chip-icon" aria-hidden>
                        {statusIcon(incident.status)}
                      </span>
                      {incident.status}
                    </span>
                    {incident.affectedService && (
                      <span className="service-chip">
                        <span className="material-icons chip-icon" aria-hidden>
                          dns
                        </span>
                        {incident.affectedService}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <p className="incident-description">{incident.description}</p>

              {/* Devin Session Status */}
              {incident.devinSessionId && (
                <div className="devin-session">
                  <div className="devin-header">
                    <span className="material-icons devin-icon" aria-hidden>
                      smart_toy
                    </span>
                    <span className="devin-label">Devin Session</span>
                    <span
                      className={`devin-status devin-${incident.devinSessionStatus || "unknown"}`}
                    >
                      {incident.devinSessionStatus || "pending"}
                    </span>
                  </div>
                  <div className="devin-details">
                    <span className="session-id">{incident.devinSessionId}</span>
                    {incident.devinSessionUrl && (
                      <a
                        href={incident.devinSessionUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="session-link"
                      >
                        <span className="material-icons" aria-hidden>
                          open_in_new
                        </span>
                        View Session
                      </a>
                    )}
                  </div>
                </div>
              )}

              {!incident.devinSessionId && incident.active && (
                <div className="devin-session devin-pending">
                  <span className="material-icons devin-icon" aria-hidden>
                    smart_toy
                  </span>
                  <span>No Devin session</span>
                  <button
                    type="button"
                    className="btn btn-stroked btn-primary trigger-btn"
                    disabled={triggeringIds.has(incident.id)}
                    onClick={() => onTriggerSession(incident)}
                  >
                    <span className="material-icons" aria-hidden>
                      play_arrow
                    </span>
                    {triggeringIds.has(incident.id) ? "Launching..." : "Launch Devin"}
                  </button>
                </div>
              )}

              <div className="incident-actions">
                {(incident.status === "open" || incident.status === "investigating") && (
                  <button
                    type="button"
                    className="btn btn-stroked btn-primary"
                    onClick={() => onResolve(incident)}
                  >
                    <span className="material-icons" aria-hidden>
                      check_circle
                    </span>{" "}
                    Resolve
                  </button>
                )}
                {incident.status === "resolved" && (
                  <button
                    type="button"
                    className="btn btn-stroked"
                    onClick={() => onClose(incident)}
                  >
                    <span className="material-icons" aria-hidden>
                      cancel
                    </span>{" "}
                    Close
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-stroked btn-warn"
                  onClick={() => onDelete(incident)}
                >
                  <span className="material-icons" aria-hidden>
                    delete
                  </span>{" "}
                  Delete
                </button>
              </div>

              <div className="incident-meta">
                <span>Created {formatMedium(incident.createdAt)}</span>
                {incident.resolvedAt && <span>Resolved {formatMedium(incident.resolvedAt)}</span>}
                {incident.closedAt && <span>Closed {formatMedium(incident.closedAt)}</span>}
              </div>
            </article>
          ))}
        </div>
      )}

      {/* Empty state (filter active, no matches) */}
      {!loading && filteredIncidents.length === 0 && filterStatus && (
        <div className="empty-state">
          <span className="material-icons" aria-hidden>
            filter_list_off
          </span>
          <p>No {filterStatus} incidents</p>
          <button type="button" className="btn btn-stroked" onClick={() => setFilterStatus("")}>
            Clear Filter
          </button>
        </div>
      )}

      {/* First-run onboarding (no incidents at all) */}
      {!loading && !incidentsQuery.isError && incidents.length === 0 && !filterStatus && (
        <div className="onboarding">
          <div className="card onboarding-card">
            <div className="onboarding-hero">
              <span className="material-icons onboarding-icon" aria-hidden>
                smart_toy
              </span>
              <h2>Automated Incident Response</h2>
              <p>
                Report an incident and Devin will automatically spin up a session to investigate
                the root cause across OtterWorks' 11 microservices.
              </p>
            </div>
            <div className="onboarding-steps">
              <div className="step">
                <div className="step-number">1</div>
                <div>
                  <strong>Report an incident</strong>
                  <br />
                  Describe the issue, select severity and affected service
                </div>
              </div>
              <div className="step">
                <div className="step-number">2</div>
                <div>
                  <strong>Devin session launches</strong>
                  <br />
                  An AI session is created with full architecture context
                </div>
              </div>
              <div className="step">
                <div className="step-number">3</div>
                <div>
                  <strong>Track progress</strong>
                  <br />
                  Monitor the session status and view Devin's investigation live
                </div>
              </div>
            </div>
            <button
              type="button"
              className="btn btn-raised btn-warn onboarding-cta"
              onClick={() => setShowCreateForm(true)}
            >
              <span className="material-icons" aria-hidden>
                add_alert
              </span>
              Report Your First Incident
            </button>
          </div>
        </div>
      )}

      {/* Error state for the initial list load */}
      {!loading && incidentsQuery.isError && (
        <div className="empty-state error-state">
          <span className="material-icons" aria-hidden>
            error_outline
          </span>
          <p>Failed to load incidents</p>
          <button
            type="button"
            className="btn btn-stroked"
            onClick={() => incidentsQuery.refetch()}
          >
            Retry
          </button>
        </div>
      )}

      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title ?? ""}
        message={confirm?.message ?? ""}
        confirmText={confirm?.confirmText ?? ""}
        confirmColor={confirm?.confirmColor}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          confirm?.onConfirm();
          setConfirm(null);
        }}
      />
    </div>
  );
}
