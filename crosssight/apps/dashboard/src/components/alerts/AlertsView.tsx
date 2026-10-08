"use client";

import { ScatterplotLayer } from "@deck.gl/layers";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { DeckMap, flyTo } from "@/components/map/DeckMap";
import { ErrorState } from "@/components/common/ErrorState";
import { AlertTypeIcon } from "@/components/ui/AlertTypeIcon";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Kpi } from "@/components/ui/Kpi";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Timestamp } from "@/components/ui/Timestamp";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { LiveSocket } from "@/lib/ws";
import { cn, severityRank, startOfTodayIst, titleCase } from "@/lib/utils";
import type { Alert, Camera } from "@/types";
import type { Map as MapLibreMap } from "maplibre-gl";

export function AlertsView() {
  const params = useSearchParams();
  const mapRef = useRef<MapLibreMap | null>(null);
  const flatRef = useRef<Alert[]>([]);
  const cursorRef = useRef(0);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selected, setSelected] = useState<Alert | null>(null);
  const [cursor, setCursor] = useState(0);
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [severity, setSeverity] = useState("");
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [closeNote, setCloseNote] = useState("");
  const [dispatchTo, setDispatchTo] = useState("");
  const [actionLoading, setActionLoading] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(true);
  const [closedOpen, setClosedOpen] = useState(false);
  const [dialog, setDialog] = useState<null | "dispatch" | "close">(null);
  const [showHints, setShowHints] = useState(true);
  const hintTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    setBooting(true);
    const query: Record<string, string> = {};
    if (status) query.status = status;
    if (type) query.type = type;
    if (severity) query.severity = severity;
    const settled = await Promise.allSettled([api.alerts(query), api.cameras()]);
    const failures = settled.filter((result) => result.status === "rejected").length;
    if (failures === settled.length) {
      setError("Failed to load alerts");
      setBooting(false);
      return;
    }
    setError(null);
    if (settled[0].status === "fulfilled") setAlerts(settled[0].value);
    else toast.error("Failed to load alerts. Retry", () => { void load(); });
    if (settled[1].status === "fulfilled") setCameras(settled[1].value);
    else toast.error("Failed to load cameras. Retry", () => { void load(); });
    setBooting(false);
  }, [status, type, severity]);

  useEffect(() => {
    void load();
  }, [load]);

  const openDetail = useCallback(async (id: string) => {
    try {
      const detail = await api.alert(id);
      setSelected(detail);
      setCloseNote("");
      setDispatchTo(detail.dispatched_to ?? "");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load alert");
    }
  }, []);

  useEffect(() => {
    const id = params.get("id");
    if (id) void openDetail(id);
  }, [params, openDetail]);

  useEffect(() => {
    const socket = new LiveSocket();
    socket.connect();
    socket.subscribe(["alerts"]);
    const off = socket.onMessage((msg) => {
      if (msg.channel !== "alerts") return;
      const alert = msg.data as Alert;
      setAlerts((prev) => {
        const exists = prev.find((row) => row.id === alert.id);
        if (exists) return prev.map((row) => (row.id === alert.id ? { ...row, ...alert } : row));
        return [alert, ...prev];
      });
    });
    return () => {
      off();
      socket.close();
    };
  }, []);

  const todayStart = startOfTodayIst().getTime();
  const active = useMemo(
    () =>
      alerts
        .filter((alert) => (alert.status ?? "new") === "new")
        .sort((a, b) => severityRank(a.severity) - severityRank(b.severity) || Date.parse(b.ts ?? "") - Date.parse(a.ts ?? "")),
    [alerts],
  );
  const inReview = useMemo(
    () => alerts.filter((alert) => alert.status === "reviewing" || alert.status === "dispatched"),
    [alerts],
  );
  const closedToday = useMemo(
    () =>
      alerts.filter((alert) => alert.status === "closed" && Date.parse(alert.ts ?? alert.created_at ?? "") >= todayStart),
    [alerts, todayStart],
  );
  const flat = useMemo(() => [...active, ...inReview, ...closedToday], [active, inReview, closedToday]);
  flatRef.current = flat;
  cursorRef.current = cursor;

  useEffect(() => {
    if (cursor > flat.length - 1) setCursor(Math.max(0, flat.length - 1));
  }, [cursor, flat.length]);

  useEffect(() => {
    document.querySelector("[data-alert-cursor='true']")?.scrollIntoView({ block: "nearest" });
  }, [cursor, flat]);

  const revealHints = useCallback(() => {
    setShowHints(true);
    if (hintTimer.current) window.clearTimeout(hintTimer.current);
    hintTimer.current = window.setTimeout(() => setShowHints(false), 10000);
  }, []);

  useEffect(() => {
    revealHints();
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) {
        return;
      }
      revealHints();
      const current = flatRef.current[cursorRef.current];
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setCursor((index) => (flatRef.current.length === 0 ? 0 : (index + 1) % flatRef.current.length));
        const next = flatRef.current[(cursorRef.current + 1) % Math.max(1, flatRef.current.length)];
        if (next && inReview.some((alert) => alert.id === next.id)) setReviewOpen(true);
        if (next && closedToday.some((alert) => alert.id === next.id)) setClosedOpen(true);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setCursor((index) => (flatRef.current.length === 0 ? 0 : (index - 1 + flatRef.current.length) % flatRef.current.length));
      } else if (event.key.toLowerCase() === "a" && current?.id) {
        event.preventDefault();
        void runAction("ack", current);
      } else if (event.key.toLowerCase() === "d" && current?.id) {
        event.preventDefault();
        setSelected(current);
        setDialog("dispatch");
      } else if (event.key.toLowerCase() === "r" && current?.id) {
        event.preventDefault();
        setSelected(current);
        setDialog("close");
      } else if (event.key.toLowerCase() === "v" && current) {
        event.preventDefault();
        centerOn(current);
      } else if (event.key === "Enter" && current?.id) {
        event.preventDefault();
        void openDetail(current.id);
      } else if (event.key === "Escape") {
        setDialog(null);
        setSelected(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closedToday, inReview, openDetail, revealHints]);

  function centerOn(alert: Alert) {
    const cameraId = alert.camera_ids?.[0];
    const camera = cameras.find((item) => item.id === cameraId);
    if (camera && mapRef.current) flyTo(mapRef.current, camera.lng, camera.lat, 15);
  }

  async function runAction(action: "ack" | "dispatch" | "close", alert = selected) {
    if (!alert?.id) return;
    setActionLoading(true);
    try {
      let updated: Alert;
      if (action === "ack") updated = await api.ackAlert(alert.id);
      else if (action === "dispatch") updated = await api.dispatchAlert(alert.id, dispatchTo);
      else updated = await api.closeAlert(alert.id, closeNote);
      setSelected(updated);
      setAlerts((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
      setDialog(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActionLoading(false);
    }
  }

  async function review(next: "reviewing" | "approved" | "dismissed") {
    if (!selected?.id) return;
    setActionLoading(true);
    try {
      const updated = await api.reviewAlert(selected.id, next, closeNote || "reviewed");
      setSelected(updated);
      setAlerts((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Review failed");
    } finally {
      setActionLoading(false);
    }
  }

  const evidencePoints = useMemo(() => {
    if (!selected) return [];
    const evidence = selected.evidence as { reads?: Array<{ camera_id?: string }> } | undefined;
    const reads = evidence?.reads ?? [];
    const ids = new Set([
      ...(selected.camera_ids ?? []),
      ...reads.map((read) => read.camera_id).filter(Boolean) as string[],
    ]);
    return cameras.filter((camera) => ids.has(camera.id));
  }, [selected, cameras]);

  const miniMapLayers = useMemo(
    () => [
      new ScatterplotLayer<Camera>({
        id: "evidence-cams",
        data: evidencePoints,
        getPosition: (d) => [d.lng, d.lat],
        getRadius: 80,
        radiusMinPixels: 8,
        getFillColor: [239, 68, 68, 220],
      }),
    ],
    [evidencePoints],
  );

  const cursorId = flat[cursor]?.id;

  if (booting && alerts.length === 0 && !error) {
    return (
      <div className="h-full p-4 space-y-2">
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton key={index} className="h-10" />
        ))}
      </div>
    );
  }

  if (error && alerts.length === 0) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="h-full flex flex-col">
      <div className="shrink-0 border-b border-border bg-surface-raised px-4 py-3 flex flex-wrap gap-3 items-end">
        <div className="w-40">
          <Kpi variant="hero" label="Active" value={String(active.length)} tone={active.length > 0 ? "danger" : "success"} />
        </div>
        <Select label="Status" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All</option>
          <option value="new">New</option>
          <option value="acknowledged">Acknowledged</option>
          <option value="dispatched">Dispatched</option>
          <option value="closed">Closed</option>
          <option value="reviewing">Reviewing</option>
          <option value="approved">Approved</option>
          <option value="dismissed">Dismissed</option>
        </Select>
        <Select label="Type" value={type} onChange={(event) => setType(event.target.value)}>
          <option value="">All</option>
          <option value="watchlist">Watchlist</option>
          <option value="cloned_plate">Cloned plate</option>
          <option value="convoy">Convoy</option>
          <option value="loitering">Loitering</option>
          <option value="geofence">Geofence</option>
          <option value="wrong_way">Wrong way</option>
          <option value="route_anomaly">Route anomaly</option>
          <option value="plate_vehicle_mismatch">Plate/vehicle mismatch</option>
          <option value="stopped_vehicle">Stopped vehicle</option>
          <option value="camera_health">Camera health</option>
        </Select>
        <Select label="Severity" value={severity} onChange={(event) => setSeverity(event.target.value)}>
          <option value="">All</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="critical">Critical</option>
        </Select>
        <Button variant="secondary" size="sm" onClick={() => void load()}>Apply</Button>
      </div>

      <div className="flex-1 min-h-0 flex">
        <aside className="w-96 border-r border-border overflow-y-auto bg-surface-raised">
          <Section title="Active" count={active.length} open>
            {active.map((alert) => (
              <AlertRow key={alert.id ?? alert.plate_norm} alert={alert} selected={alert.id === cursorId} onSelect={() => alert.id && void openDetail(alert.id)} />
            ))}
          </Section>
          <Section title="In review" count={inReview.length} open={reviewOpen} onToggle={() => setReviewOpen((value) => !value)}>
            {inReview.map((alert) => (
              <AlertRow key={alert.id ?? alert.plate_norm} alert={alert} selected={alert.id === cursorId} onSelect={() => alert.id && void openDetail(alert.id)} />
            ))}
          </Section>
          <Section title="Closed today" count={closedToday.length} open={closedOpen} onToggle={() => setClosedOpen((value) => !value)}>
            {closedToday.map((alert) => (
              <AlertRow key={alert.id ?? alert.plate_norm} alert={alert} selected={alert.id === cursorId} onSelect={() => alert.id && void openDetail(alert.id)} />
            ))}
          </Section>
        </aside>

        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex-1 min-h-0">
            <DeckMap layers={miniMapLayers} onMapReady={(map) => { mapRef.current = map; }} />
          </div>
          {selected && (
            <aside className="h-72 border-t border-border bg-surface-raised overflow-y-auto p-4 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-mono text-lg text-accent">{selected.plate_norm}</p>
                  <p className="text-sm text-muted inline-flex items-center gap-2">
                    <AlertTypeIcon type={selected.type} />
                    {selected.type.replace(/_/g, " ")}
                  </p>
                </div>
                <Button variant="ghost" size="sm" aria-label="Close alert detail" onClick={() => setSelected(null)}>Close</Button>
              </div>
              {selected.needs_verification && (
                <div className="rounded border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">
                  Fuzzy match — needs verification before dispatch.
                </div>
              )}
              <p className="text-xs text-muted">Cameras: {(selected.camera_ids ?? []).join(", ") || "—"}</p>
              <div className="flex flex-wrap gap-2">
                <Badge tone={selected.severity === "high" || selected.severity === "critical" ? "danger" : "warning"}>{selected.severity}</Badge>
                <Badge case="normal">{titleCase(selected.status ?? "new")}</Badge>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={actionLoading} onClick={() => void runAction("ack")}>Acknowledge</Button>
                <Button size="sm" variant="secondary" disabled={actionLoading} onClick={() => void review("reviewing")}>Review</Button>
                <Button size="sm" variant="secondary" disabled={actionLoading || !closeNote.trim()} onClick={() => void review("approved")}>Approve</Button>
                <Button size="sm" variant="secondary" disabled={actionLoading || !closeNote.trim()} onClick={() => void review("dismissed")}>Dismiss</Button>
                <Button size="sm" variant="secondary" disabled={actionLoading} onClick={() => setDialog("dispatch")}>Dispatch</Button>
                <Button size="sm" variant="danger" disabled={actionLoading} onClick={() => setDialog("close")}>Close</Button>
              </div>
            </aside>
          )}
        </div>
      </div>

      {showHints && (
        <footer className="shrink-0 border-t border-border px-4 py-2 text-label">
          ↑↓ move  A ack  D dispatch  R resolve  V map
        </footer>
      )}

      {dialog && (
        <dialog
          ref={(node) => {
            if (node && !node.open) node.showModal();
          }}
          className="rounded-lg border border-border bg-surface-raised text-slate-100 p-4 w-80"
          onClose={() => setDialog(null)}
        >
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              void runAction(dialog === "dispatch" ? "dispatch" : "close");
            }}
          >
            {dialog === "dispatch" ? (
              <Input label="Dispatch to" value={dispatchTo} onChange={(event) => setDispatchTo(event.target.value)} placeholder="unit-id" autoFocus />
            ) : (
              <Input label="Close note" value={closeNote} onChange={(event) => setCloseNote(event.target.value)} autoFocus />
            )}
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={actionLoading || (dialog === "close" && !closeNote.trim()) || (dialog === "dispatch" && !dispatchTo)}>
                {dialog === "dispatch" ? "Dispatch" : "Resolve"}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setDialog(null)}>Cancel</Button>
            </div>
          </form>
        </dialog>
      )}
    </div>
  );
}

function Section({
  title,
  count,
  open,
  onToggle,
  children,
}: {
  title: string;
  count: number;
  open: boolean;
  onToggle?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-border">
      {onToggle ? (
        <button type="button" className="w-full flex items-center gap-2 px-3 py-[var(--row-py)] text-left" onClick={onToggle}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span className="text-label">{title}</span>
          <span className="ml-auto text-xs font-mono">{count}</span>
        </button>
      ) : (
        <div className="px-3 py-[var(--row-py)] text-label">{title} · {count}</div>
      )}
      {open && <div>{children}</div>}
    </section>
  );
}

function AlertRow({ alert, selected, onSelect }: { alert: Alert; selected: boolean; onSelect: () => void }) {
  const severe = alert.severity === "high" || alert.severity === "critical";
  return (
    <button
      type="button"
      data-alert-cursor={selected ? "true" : "false"}
      onClick={onSelect}
      className={cn(
        "w-full text-left px-3 py-[var(--row-py)] border-l-2 text-xs",
        selected ? "bg-accent/20 border-l-accent" : "border-l-transparent hover:bg-surface-overlay",
      )}
    >
      <div className="flex items-center gap-2">
        <AlertTypeIcon type={alert.type} className={severe ? "text-danger" : "text-warning"} />
        <Badge tone={severe ? "danger" : "warning"}>{alert.severity}</Badge>
        <span className="font-mono text-accent">{alert.plate_norm}</span>
        <Badge case="normal" className="ml-auto">{titleCase(alert.status ?? "new")}</Badge>
      </div>
      <p className="text-muted mt-1">{alert.ts ? <Timestamp iso={alert.ts} /> : "—"}</p>
    </button>
  );
}
