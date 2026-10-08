"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "@/components/common/ErrorState";
import { LoadingState } from "@/components/common/LoadingState";
import { Button } from "@/components/ui/Button";
import { CameraStatusDot } from "@/components/ui/CameraStatusDot";
import { Input } from "@/components/ui/Input";
import { Card, CardHeader } from "@/components/ui/Card";
import { Timestamp } from "@/components/ui/Timestamp";
import { api } from "@/lib/api";
import type { AuditEntry, Camera, WatchlistEntry, Zone } from "@/types";

type Tab = "cameras" | "zones" | "watchlist" | "audit";

export function AdminView() {
  const [tab, setTab] = useState<Tab>("cameras");
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [newPlate, setNewPlate] = useState("");
  const [newReason, setNewReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cams, z, wl, au] = await Promise.all([
        api.cameras(),
        api.zones(),
        api.watchlist(),
        api.audit(),
      ]);
      setCameras(cams);
      setZones(z);
      setWatchlist(wl);
      setAudit(au);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load admin data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function addWatchlist() {
    if (!newPlate || !newReason) return;
    try {
      await api.addWatchlist({ plate_norm: newPlate, reason: newReason, severity: "high" });
      setNewPlate("");
      setNewReason("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add watchlist entry");
    }
  }

  async function importCsv(file: File) {
    try {
      await api.importWatchlistCsv(file);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "CSV import failed");
    }
  }

  if (loading) return <LoadingState label="Loading admin…" />;
  if (error) return <ErrorState message={error} onRetry={load} />;

  const tabs: { id: Tab; label: string }[] = [
    { id: "cameras", label: "Cameras" },
    { id: "zones", label: "Zones" },
    { id: "watchlist", label: "Watchlist" },
    { id: "audit", label: "Audit log" },
  ];

  return (
    <div className="h-full flex flex-col">
      <div className="shrink-0 border-b border-border bg-surface-raised px-4 flex gap-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm border-b-2 -mb-px ${
              tab === t.id ? "border-accent text-white" : "border-transparent text-muted"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-auto p-4">
        {tab === "cameras" && (
          <Card>
            <CardHeader title={`Cameras (${cameras.length})`} />
            <div className="overflow-x-auto">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th>Status</th>
                    <th>Lanes</th>
                    <th>Coords</th>
                  </tr>
                </thead>
                <tbody>
                  {cameras.map((c) => (
                    <tr key={c.id}>
                      <td className="font-mono text-xs">{c.id}</td>
                      <td>{c.name}</td>
                      <td>
                        <span className="inline-flex items-center gap-2">
                          <CameraStatusDot status={c.status} />
                          {c.status}
                        </span>
                      </td>
                      <td>{c.lanes}</td>
                      <td className="font-mono text-xs">{c.lat.toFixed(4)}, {c.lng.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {tab === "zones" && (
          <Card>
            <CardHeader title={`Zones (${zones.length})`} />
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Name</th>
                  <th>Kind</th>
                </tr>
              </thead>
              <tbody>
                {zones.map((z) => (
                  <tr key={z.id}>
                    <td className="font-mono text-xs">{z.id}</td>
                    <td>{z.name}</td>
                    <td>{z.kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}

        {tab === "watchlist" && (
          <div className="space-y-4">
            <Card>
              <CardHeader title="Add entry" />
              <div className="p-4 grid md:grid-cols-3 gap-3 items-end">
                <Input label="Plate" value={newPlate} onChange={(e) => setNewPlate(e.target.value)} />
                <Input label="Reason" value={newReason} onChange={(e) => setNewReason(e.target.value)} />
                <Button onClick={addWatchlist}>Add</Button>
              </div>
            </Card>
            <Card>
              <CardHeader
                title={`Watchlist (${watchlist.length})`}
                action={
                  <label className="text-xs">
                    <input
                      type="file"
                      accept=".csv"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) importCsv(f);
                      }}
                    />
                    <span className="cursor-pointer text-accent hover:underline">Import CSV</span>
                  </label>
                }
              />
              <table>
                <thead>
                  <tr>
                    <th>Plate</th>
                    <th>Reason</th>
                    <th>Severity</th>
                    <th>Added by</th>
                  </tr>
                </thead>
                <tbody>
                  {watchlist.map((w) => (
                    <tr key={w.plate_norm}>
                      <td className="font-mono">{w.plate_norm}</td>
                      <td>{w.reason}</td>
                      <td>{w.severity}</td>
                      <td className="text-muted text-xs">{w.added_by}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>
        )}

        {tab === "audit" && (
          <Card>
            <CardHeader title="Audit log" />
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Action</th>
                  <th>Plate</th>
                  <th>Case</th>
                  <th>User</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((a) => (
                  <tr key={a.id}>
                    <td className="text-xs text-muted"><Timestamp iso={a.ts} /></td>
                    <td>{a.action}</td>
                    <td className="font-mono text-xs">{a.plate_norm ?? "—"}</td>
                    <td className="font-mono text-xs">{a.case_id ?? "—"}</td>
                    <td className="text-xs">{a.user_id ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </div>
    </div>
  );
}
