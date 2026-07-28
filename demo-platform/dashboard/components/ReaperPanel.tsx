"use client";

import { useEffect, useState } from "react";
import { apiGet, apiSend } from "@/lib/client";
import type { Orphan, ReaperConfig } from "@/lib/types";

export default function ReaperPanel() {
  const [cfg, setCfg] = useState<ReaperConfig | null>(null);
  const [orphans, setOrphans] = useState<Orphan[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function load() {
    setError(null);
    try {
      const [c, o] = await Promise.all([
        apiGet<ReaperConfig>("/api/reaper"),
        apiGet<Orphan[]>("/api/reaper/orphans").catch(() => [] as Orphan[]),
      ]);
      setCfg(c);
      setOrphans(o);
    } catch (err) {
      setError(err instanceof Error ? err.message : "load failed");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!cfg) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await apiSend<ReaperConfig>("/api/reaper", "PUT", {
        schedule_cron: cfg.scheduleCron,
        grace_seconds: cfg.graceSeconds,
        enabled: cfg.enabled,
        sweep_orphans: cfg.sweepOrphans,
        suspend_idle: cfg.suspendIdle,
        idle_after_seconds: cfg.idleAfterSeconds,
        sweep_infra: cfg.sweepInfra,
        sweep_infra_delete: cfg.sweepInfraDelete,
      });
      setCfg(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  if (!cfg) {
    return <p className="text-sm text-slate-500">{error ?? "Loading reaper config…"}</p>;
  }

  return (
    <div className="grid gap-6 md:grid-cols-2">
      <form onSubmit={save} className="rounded-lg border border-slate-200 bg-white p-6">
        <h3 className="text-base font-semibold">Reaper schedule</h3>
        <div className="mt-4 space-y-4">
          <label className="block text-sm">
            <span className="font-medium text-slate-700">Schedule (cron)</span>
            <input
              className="input mt-1 font-mono"
              value={cfg.scheduleCron}
              onChange={(e) => setCfg({ ...cfg, scheduleCron: e.target.value })}
            />
          </label>
          <label className="block text-sm">
            <span className="font-medium text-slate-700">Grace seconds</span>
            <input
              type="number"
              min={0}
              className="input mt-1"
              value={cfg.graceSeconds}
              onChange={(e) => setCfg({ ...cfg, graceSeconds: Number(e.target.value) })}
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cfg.enabled}
              onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
            />
            <span className="font-medium text-slate-700">Enabled</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cfg.sweepOrphans}
              onChange={(e) => setCfg({ ...cfg, sweepOrphans: e.target.checked })}
            />
            <span className="font-medium text-slate-700">Sweep orphans</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cfg.suspendIdle}
              onChange={(e) => setCfg({ ...cfg, suspendIdle: e.target.checked })}
            />
            <span className="font-medium text-slate-700">Suspend idle tenants</span>
          </label>
          <label className="block text-sm">
            <span className="font-medium text-slate-700">Idle after (seconds)</span>
            <input
              type="number"
              min={60}
              className="input mt-1"
              value={cfg.idleAfterSeconds}
              onChange={(e) => setCfg({ ...cfg, idleAfterSeconds: Number(e.target.value) })}
            />
            <span className="mt-1 block text-xs text-slate-500">
              Zero ingress requests for this long scales the tenant to zero. Its namespace,
              config and database are kept, so check-out wakes it in seconds.
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cfg.sweepInfra}
              onChange={(e) => setCfg({ ...cfg, sweepInfra: e.target.checked })}
            />
            <span className="font-medium text-slate-700">Sweep AWS infrastructure orphans</span>
          </label>
          <p className="text-xs text-slate-500">
            Finds load balancers, volumes, addresses and security groups that Kubernetes
            created and no longer owns. Only resources carrying an OtterWorks or cluster
            ownership tag are ever considered; anything untagged is reported, never touched.
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              disabled={!cfg.sweepInfra}
              checked={cfg.sweepInfraDelete}
              onChange={(e) => setCfg({ ...cfg, sweepInfraDelete: e.target.checked })}
            />
            <span className="font-medium text-slate-700">…and delete what it finds</span>
          </label>
          <p className="text-xs text-slate-500">
            Off, the sweep only reports. Review a report-only run before arming this: the
            account holds resources this platform did not create.
          </p>
        </div>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
        {saved && <p className="mt-3 text-sm text-green-600">Saved.</p>}
        <button type="submit" disabled={busy} className="btn-primary mt-6">
          {busy ? "Saving…" : "Save"}
        </button>
      </form>

      <div className="rounded-lg border border-slate-200 bg-white p-6">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold">Orphans preview</h3>
          <button className="btn-xs" onClick={() => void load()}>
            Refresh
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Live namespaces with no matching tenant record.
        </p>
        {orphans.length === 0 ? (
          <p className="mt-4 text-sm text-slate-500">No orphans detected.</p>
        ) : (
          <ul className="mt-4 space-y-1 text-sm">
            {orphans.map((o) => (
              <li key={o.name} className="font-mono">
                {o.name} <span className="text-slate-400">({o.kind})</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
