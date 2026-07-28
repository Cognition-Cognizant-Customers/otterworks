"use client";

import { useState } from "react";
import { apiSend } from "@/lib/client";
import { formatCountdown } from "@/lib/format";
import StatusBadge from "@/components/StatusBadge";
import type { Tenant } from "@/lib/types";

export default function TenantsTable({
  tenants,
  onChanged,
}: {
  tenants: Tenant[];
  onChanged: () => void;
}) {
  if (tenants.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 p-10 text-center text-sm text-slate-500">
        No tenants yet. Use “Checkout” to reserve one.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <Th>Id</Th>
            <Th>Status</Th>
            <Th>Owner</Th>
            <Th>Branch</Th>
            <Th>Tier</Th>
            <Th>URL</Th>
            <Th>Pods</Th>
            <Th>Expires</Th>
            <Th>Actions</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {tenants.map((t) => (
            <Row key={t.id} tenant={t} onChanged={onChanged} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({ tenant, onChanged }: { tenant: Tenant; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "checkin" | "extend" | "inject" | "redeploy") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "checkin") {
        await apiSend(`/api/tenants/${tenant.id}/checkin`, "POST");
      } else if (kind === "extend") {
        await apiSend(`/api/tenants/${tenant.id}/extend`, "POST", { ttl: "8h" });
      } else if (kind === "redeploy") {
        await apiSend(`/api/tenants/${tenant.id}/redeploy`, "POST");
      } else {
        const scenario = window.prompt("Scenario to inject (e.g. upload_s3_error):");
        if (!scenario) {
          setBusy(null);
          return;
        }
        await apiSend(`/api/tenants/${tenant.id}/inject`, "POST", { scenario });
      }
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "action failed");
    } finally {
      setBusy(null);
    }
  }

  const ready = tenant.live ? `${tenant.live.readyPods}/${tenant.live.totalPods}` : "—";

  return (
    <tr className="hover:bg-slate-50">
      <Td className="font-mono">
        {tenant.id}
        {tenant.persistent && (
          <span
            className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-600"
            title="Perpetual: exempt from the reaper and from idle-suspend"
          >
            perpetual
          </span>
        )}
      </Td>
      <Td>
        <StatusBadge status={tenant.status} />
      </Td>
      <Td>{tenant.owner ?? "—"}</Td>
      <Td className="font-mono text-xs">{tenant.branch ?? "—"}</Td>
      <Td>{tenant.tier}</Td>
      <Td>
        {tenant.url ? (
          <a
            href={tenant.url}
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 hover:underline"
          >
            open
          </a>
        ) : (
          "—"
        )}
      </Td>
      <Td>{ready}</Td>
      <Td>
        {tenant.persistent ? (
          <span className="text-xs text-slate-500">never</span>
        ) : (
          formatCountdown(tenant.expiresAt)
        )}
      </Td>
      <Td>
        {/* A perpetual tenant hides the actions that would tear it down or
            break it. The API refuses them too; this only saves the attempt. */}
        <div className="flex gap-1">
          <button className="btn-xs" disabled={busy !== null} onClick={() => act("redeploy")}>
            {busy === "redeploy" ? "…" : "Redeploy"}
          </button>
          {!tenant.persistent && (
            <>
              <button className="btn-xs" disabled={busy !== null} onClick={() => act("checkin")}>
                {busy === "checkin" ? "…" : "Check-in"}
              </button>
              <button className="btn-xs" disabled={busy !== null} onClick={() => act("extend")}>
                {busy === "extend" ? "…" : "Extend"}
              </button>
              <button className="btn-xs" disabled={busy !== null} onClick={() => act("inject")}>
                {busy === "inject" ? "…" : "Inject"}
              </button>
            </>
          )}
        </div>
        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      </Td>
    </tr>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2 font-medium">{children}</th>;
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-3 py-2 ${className ?? ""}`}>{children}</td>;
}
