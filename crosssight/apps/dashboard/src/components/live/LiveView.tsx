"use client";

import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { Bell, ChevronDown, ChevronRight, Radio } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { DeckMap, flyTo } from "@/components/map/DeckMap";
import { ErrorState } from "@/components/common/ErrorState";
import { AlertTypeIcon } from "@/components/ui/AlertTypeIcon";
import { Badge } from "@/components/ui/Badge";
import { Kpi } from "@/components/ui/Kpi";
import { Skeleton } from "@/components/ui/Skeleton";
import { Timestamp } from "@/components/ui/Timestamp";
import { api } from "@/lib/api";
import { alertInSource, cameraInSource, sourceMode } from "@/lib/source";
import { getPrefs, updatePrefs } from "@/lib/prefs";
import { toast } from "@/lib/toast";
import { LiveSocket } from "@/lib/ws";
import {
  cameraRingColor,
  congestionColor,
  formatIstTime,
  h3IntToString,
  severityRank,
  volumeFillColor,
} from "@/lib/utils";
import type { Alert, Camera, FlowWindow, HeatmapWsPayload, LiveRead, SegmentCongestion } from "@/types";
import type { Map as MapLibreMap } from "maplibre-gl";

const EmptyState = dynamic(() => import("@/components/ui/EmptyState").then((mod) => mod.EmptyState), { ssr: false });

interface HeatCell {
  h3: string;
  count: number;
}

const SCRUB_ENABLED =
  process.env.NEXT_PUBLIC_ENABLE_TIME_SCRUB === "true" || process.env.NEXT_PUBLIC_ENABLE_TIME_SCRUB === "1";

export function LiveView() {
  const params = useSearchParams();
  const focusId = params.get("focus");
  const mapRef = useRef<MapLibreMap | null>(null);
  const watchPlates = useRef<Set<string>>(new Set());
  const readStamps = useRef<number[]>([]);
  const readsTimer = useRef<number | null>(null);
  const alertsTimer = useRef<number | null>(null);

  const [cameras, setCameras] = useState<Camera[]>([]);
  const [segments, setSegments] = useState<SegmentCongestion[]>([]);
  const [heatmap, setHeatmap] = useState<Map<string, number>>(new Map());
  const [heatmapStale, setHeatmapStale] = useState(false);
  const [heatmapLatest, setHeatmapLatest] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [criticalActive, setCriticalActive] = useState(0);
  const [flowByCamera, setFlowByCamera] = useState<Map<string, FlowWindow>>(new Map());
  const [routeCorridors, setRouteCorridors] = useState<
    Array<{ camera_a: string; camera_b: string; hop_count: number }>
  >([]);
  const [reads, setReads] = useState<LiveRead[]>([]);
  const [readTotal, setReadTotal] = useState(0);
  const [readsPerMin, setReadsPerMin] = useState(0);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [readsOpen, setReadsOpen] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [offsetMin, setOffsetMin] = useState(0);

  const applyHeat = useCallback((hm: { cells: { h3: string | number; count: number }[]; stale?: boolean; latest_ts?: string | null }) => {
    const hmMap = new Map<string, number>();
    hm.cells.forEach((cell) => hmMap.set(String(cell.h3), cell.count));
    setHeatmap(hmMap);
    setHeatmapStale(Boolean(hm.stale));
    setHeatmapLatest(hm.latest_ts ?? null);
  }, []);

  const load = useCallback(async () => {
    setBooting(true);
    const at = new Date().toISOString();
    const pull = () =>
      Promise.allSettled([api.cameras(), api.segments(at), api.heatmap("15m"), api.alerts()]);
    let settled = await pull();
    if (settled.every((result) => result.status === "rejected")) {
      await new Promise((resolve) => window.setTimeout(resolve, 800));
      settled = await pull();
    }
    const labels = ["cameras", "congestion segments", "heatmap", "alerts"] as const;
    let failures = 0;
    settled.forEach((result, index) => {
      if (result.status === "rejected") {
        failures += 1;
        const label = labels[index];
        toast.error(`Failed to load ${label}. Retry`, () => {
          void load();
        });
      }
    });
    if (failures === settled.length) {
      setError("Failed to load live data");
      setBooting(false);
      return;
    }
    setError(null);
    const video = sourceMode() === "video";
    if (settled[0].status === "fulfilled") setCameras(settled[0].value);
    if (settled[1].status === "fulfilled") setSegments(video ? [] : settled[1].value.segments);
    if (settled[2].status === "fulfilled") applyHeat(settled[2].value);
    if (settled[3].status === "fulfilled") {
      const recent = settled[3].value;
      setCriticalActive(
        recent.filter(
          (alert) =>
            (alert.severity === "high" || alert.severity === "critical") && (alert.status ?? "new") === "new",
        ).length,
      );
      setAlerts(recent.slice(0, 20));
    }
    if (video) {
      setRouteCorridors([]);
    } else {
      api.routeDensity("1h").then((density) => setRouteCorridors(density.corridors ?? [])).catch(() => undefined);
    }
    api.recentReads().then((res) => {
      const rows = (res.reads ?? []) as unknown as LiveRead[];
      setReads(rows.slice(0, 30));
      setReadTotal(rows.length);
    }).catch(() => undefined);
    api.watchlist().then((rows) => {
      watchPlates.current = new Set(rows.map((row) => row.plate_norm));
    }).catch(() => undefined);
    setBooting(false);
  }, [applyHeat]);

  useEffect(() => {
    const prefs = getPrefs();
    setReadsOpen(prefs.liveSidebar.reads);
    setAlertsOpen(prefs.liveSidebar.alerts);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (sourceMode() !== "video") return;
    const id = window.setInterval(() => {
      api.heatmap("15m").then(applyHeat).catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(id);
  }, [applyHeat]);

  useEffect(() => {
    if (!focusId || !mapRef.current) return;
    const camera = cameras.find((item) => item.id === focusId);
    if (camera) flyTo(mapRef.current, camera.lng, camera.lat, 15);
  }, [focusId, cameras]);

  function flashReads() {
    setReadsOpen(true);
    if (readsTimer.current) window.clearTimeout(readsTimer.current);
    readsTimer.current = window.setTimeout(() => {
      if (!getPrefs().liveSidebar.reads) setReadsOpen(false);
    }, 10000);
  }

  function flashAlerts() {
    setAlertsOpen(true);
    if (alertsTimer.current) window.clearTimeout(alertsTimer.current);
    alertsTimer.current = window.setTimeout(() => {
      if (!getPrefs().liveSidebar.alerts) setAlertsOpen(false);
    }, 10000);
  }

  useEffect(() => {
    const socket = new LiveSocket();
    socket.connect();
    socket.subscribe(["heatmap", "alerts", "flow", "reads"]);

    const off = socket.onMessage((msg) => {
      const video = sourceMode() === "video";
      if (msg.channel === "heatmap") {
        if (video) return;
        const data = msg.data as HeatmapWsPayload;
        const h3 = h3IntToString(data.h3_cell ?? data.h3 ?? 0);
        setHeatmap((prev) => {
          const next = new Map(prev);
          next.set(h3, (next.get(h3) ?? 0) + 1);
          return next;
        });
      }
      if (msg.channel === "alerts") {
        const alert = msg.data as Alert;
        if (!alertInSource(alert.camera_ids)) return;
        setAlerts((prev) => [alert, ...prev].slice(0, 20));
        if ((alert.severity === "high" || alert.severity === "critical") && (alert.status ?? "new") === "new") {
          setCriticalActive((count) => count + 1);
          flashAlerts();
        }
      }
      if (msg.channel === "flow") {
        const fw = msg.data as FlowWindow;
        if (!cameraInSource(fw.camera_id)) return;
        setFlowByCamera((prev) => new Map(prev).set(fw.camera_id, fw));
      }
      if (msg.channel === "reads") {
        const read = msg.data as LiveRead;
        if (!cameraInSource(read.camera_id)) return;
        setReads((prev) => [read, ...prev].slice(0, 30));
        setReadTotal((count) => count + 1);
        const now = Date.now();
        readStamps.current.push(now);
        readStamps.current = readStamps.current.filter((ts) => now - ts < 60_000);
        setReadsPerMin(readStamps.current.length);
        if (watchPlates.current.has(read.plate_norm)) flashReads();
      }
    });

    return () => {
      off();
      socket.close();
    };
  }, []);

  useEffect(() => {
    if (offsetMin === 0 || !SCRUB_ENABLED) return;
    const at = new Date(Date.now() - offsetMin * 60_000).toISOString();
    api.segments(at).then((segs) => setSegments(segs.segments)).catch(() => {
      toast.error("Failed to load congestion segments. Retry", () => setOffsetMin((value) => value));
    });
    api.heatmap("15m", undefined, at).then(applyHeat).catch(() => {
      toast.error("Failed to load heatmap. Retry");
    });
  }, [offsetMin, applyHeat]);

  const heatData = useMemo<HeatCell[]>(
    () => Array.from(heatmap.entries()).map(([h3, count]) => ({ h3, count })),
    [heatmap],
  );

  const maxHeat = useMemo(
    () => Math.max(1, ...heatData.map((d) => d.count)),
    [heatData],
  );

  const cameraVolumes = useMemo(() => {
    const vols = new Map<string, number>();
    flowByCamera.forEach((fw, id) => vols.set(id, fw.volume ?? 0));
    return vols;
  }, [flowByCamera]);

  const maxVolume = useMemo(() => Math.max(1, ...Array.from(cameraVolumes.values())), [cameraVolumes]);

  const kpis = useMemo(() => {
    const vehicles15m = heatData.reduce((sum, cell) => sum + cell.count, 0);
    const speeds = Array.from(flowByCamera.values())
      .map((flow) => flow.avg_speed_kmh)
      .filter((speed): speed is number => speed != null);
    let avgSpeed = speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : null;
    if (avgSpeed == null) {
      const segSpeeds = segments
        .map((segment) => segment.median_speed_kmh)
        .filter((speed): speed is number => speed != null && speed > 0);
      if (segSpeeds.length) avgSpeed = segSpeeds.reduce((a, b) => a + b, 0) / segSpeeds.length;
    }
    const congested = segments.filter((segment) => segment.congestion_index >= 0.5).length;
    return { vehicles15m, avgSpeed, congested };
  }, [heatData, flowByCamera, segments]);

  const topAlert = useMemo(
    () => [...alerts].sort((a, b) => severityRank(a.severity) - severityRank(b.severity))[0],
    [alerts],
  );

  const layers = useMemo(() => {
    const heatLayer = new H3HexagonLayer<HeatCell>({
      id: "heatmap",
      data: heatData,
      pickable: false,
      extruded: true,
      getHexagon: (d) => d.h3,
      getFillColor: (d) => {
        const t = d.count / maxHeat;
        return [56, 189, 248, Math.round(40 + t * 180)] as [number, number, number, number];
      },
      getElevation: (d) => Math.sqrt(d.count) * 80,
      elevationScale: 1,
      coverage: 0.9,
    });

    const maxHops = Math.max(1, ...routeCorridors.map((c) => c.hop_count));
    const routePaths = routeCorridors
      .map((c) => {
        const a = cameras.find((cam) => cam.id === c.camera_a);
        const b = cameras.find((cam) => cam.id === c.camera_b);
        if (!a || !b) return null;
        return {
          path: [[a.lng, a.lat], [b.lng, b.lat]] as [number, number][],
          hops: c.hop_count,
          id: `${c.camera_a}-${c.camera_b}`,
        };
      })
      .filter(Boolean) as { path: [number, number][]; hops: number; id: string }[];

    const routeLayer = new PathLayer({
      id: "route-density",
      data: routePaths,
      getPath: (d) => d.path,
      getColor: (d) => {
        const t = d.hops / maxHops;
        return [251, 146, 60, Math.round(60 + t * 180)] as [number, number, number, number];
      },
      getWidth: (d) => 2 + (d.hops / maxHops) * 10,
      widthMinPixels: 1,
      pickable: true,
    });

    const segmentPaths = segments
      .map((seg) => {
        const a = cameras.find((c) => c.id === seg.camera_a);
        const b = cameras.find((c) => c.id === seg.camera_b);
        if (!a || !b) return null;
        return {
          path: [[a.lng, a.lat], [b.lng, b.lat]],
          congestion: seg.congestion_index,
          id: `${seg.camera_a}-${seg.camera_b}`,
        };
      })
      .filter(Boolean) as { path: [number, number][]; congestion: number; id: string }[];

    const pathLayer = new PathLayer({
      id: "segments",
      data: segmentPaths,
      getPath: (d) => d.path,
      getColor: (d) => congestionColor(d.congestion),
      getWidth: 4,
      widthMinPixels: 2,
      pickable: true,
    });

    const cameraLayer = new ScatterplotLayer<Camera>({
      id: "cameras",
      data: cameras,
      getPosition: (d) => [d.lng, d.lat],
      getRadius: 48,
      radiusMinPixels: 6,
      radiusMaxPixels: 14,
      getFillColor: (d) => volumeFillColor(cameraVolumes.get(d.id) ?? 0, maxVolume),
      pickable: true,
    });

    const ringLayer = new ScatterplotLayer<Camera>({
      id: "cameras-ring",
      data: cameras,
      stroked: true,
      filled: false,
      getPosition: (d) => [d.lng, d.lat],
      getRadius: 72,
      radiusMinPixels: 9,
      radiusMaxPixels: 18,
      getLineWidth: 2,
      lineWidthMinPixels: 2,
      lineWidthUnits: "pixels",
      getLineColor: (d) => cameraRingColor(d.status),
      pickable: false,
    });

    return [heatLayer, routeLayer, pathLayer, cameraLayer, ringLayer];
  }, [heatData, maxHeat, segments, cameras, cameraVolumes, maxVolume, routeCorridors]);

  function stepScrub(direction: -1 | 1) {
    const next = Math.min(15, Math.max(0, offsetMin + (direction === -1 ? 1 : -1)));
    if (next !== 0 && !SCRUB_ENABLED) {
      toast.warning("Time scrub is unavailable until NEXT_PUBLIC_ENABLE_TIME_SCRUB is enabled");
      return;
    }
    setOffsetMin(next);
  }

  function toggleReads() {
    const next = !readsOpen;
    setReadsOpen(next);
    updatePrefs({ liveSidebar: { ...getPrefs().liveSidebar, reads: next } });
  }

  function toggleAlerts() {
    const next = !alertsOpen;
    setAlertsOpen(next);
    updatePrefs({ liveSidebar: { ...getPrefs().liveSidebar, alerts: next } });
  }

  if (booting && cameras.length === 0 && !error) {
    return (
      <div className="h-full flex">
        <div className="flex-1 bg-gradient-to-br from-surface via-surface-raised to-surface-overlay" />
        <aside className="w-80 border-l border-border bg-surface-raised p-4 space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
          <Skeleton className="h-24" />
          <Skeleton className="h-40" />
        </aside>
      </div>
    );
  }

  if (error && cameras.length === 0) {
    return <ErrorState message={error} onRetry={load} />;
  }

  const pausedAt = new Date(Date.now() - offsetMin * 60_000);

  return (
    <div className="h-full flex">
      <div className="flex-1 relative">
        <DeckMap layers={layers} onMapReady={(map) => { mapRef.current = map; }} />
        {heatmapStale && (
          <div
            className="absolute top-3 left-3 z-10"
            title={heatmapLatest ? `Latest read ${heatmapLatest}` : "Heatmap window is older than wall clock"}
          >
            <Badge tone="warning">Stale heatmap</Badge>
          </div>
        )}
        <div className="absolute top-3 left-1/2 z-10 -translate-x-1/2 flex items-center gap-2 rounded border border-border bg-surface-raised/95 px-2 py-1 text-xs">
          <button type="button" className="px-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded" aria-label="Step back one minute" onClick={() => stepScrub(-1)}>←</button>
          {offsetMin === 0 ? (
            <span className="inline-flex items-center gap-1.5 text-success font-mono">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
              </span>
              LIVE
            </span>
          ) : (
            <span className="font-mono text-warning">⏸ PAUSED at {formatIstTime(pausedAt, true)}</span>
          )}
          <button type="button" className="px-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded" aria-label="Step forward one minute" onClick={() => stepScrub(1)}>→</button>
          <button
            type="button"
            className="rounded border border-border px-2 py-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            onClick={() => {
              setOffsetMin(0);
              void load();
            }}
          >
            Live
          </button>
        </div>
        <div className="absolute bottom-3 left-3 z-10 w-[200px] rounded border border-border bg-surface-raised/95 p-3 text-[10px] space-y-2">
          <p className="text-label">Segment congestion</p>
          <div className="flex items-center gap-2">
            <span className="h-1 w-6 bg-success" /> low
            <span className="h-1 w-6 bg-warning" /> medium
            <span className="h-1 w-6 bg-danger" /> high
          </div>
          <p className="text-label">Camera</p>
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-full border-2 border-success" /> healthy
            <span className="h-3 w-3 rounded-full border-2 border-warning" /> degraded
            <span className="h-3 w-3 rounded-full border-2 border-danger" /> offline
          </div>
          <div className="h-2 rounded bg-gradient-to-r from-success via-warning to-danger" title="Volume fill, low to high" />
        </div>
      </div>

      <aside className="w-80 border-l border-border bg-surface-raised flex flex-col overflow-hidden">
        <div className="p-4 grid grid-cols-1 gap-3 border-b border-border">
          <Kpi
            variant="hero"
            label="Active critical"
            value={String(criticalActive)}
            tone={criticalActive > 0 ? "danger" : "success"}
          />
          <Kpi label="Vehicles (15m)" value={kpis.vehicles15m.toLocaleString()} />
          <Kpi label="Avg speed" value={kpis.avgSpeed != null ? `${kpis.avgSpeed.toFixed(1)} km/h` : "—"} />
          <Kpi label="Congested segments" value={String(kpis.congested)} tone="warning" />
        </div>

        <section className="m-3 mb-0 border border-border rounded-lg bg-surface-overlay">
          <button type="button" className="w-full flex items-center gap-2 px-3 py-[var(--row-py)] text-left" onClick={toggleReads}>
            {readsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <span className="text-label">Live reads</span>
            <span className="ml-auto text-xs font-mono text-slate-200">{readTotal} reads · {readsPerMin}/min</span>
          </button>
          {readsOpen && (
            <div className="max-h-48 overflow-y-auto px-3 pb-3 space-y-2">
              {reads.length === 0 && <EmptyState icon={Radio} title="Listening for reads" indicator />}
              {reads.map((read, index) => (
                <div key={`${read.camera_id}-${read.ts}-${index}`} className="text-xs border border-border rounded p-2 bg-[hsl(var(--input-bg))]">
                  <div className="flex justify-between font-mono">
                    <span className="text-accent">{read.plate_norm}</span>
                    <span>{Math.round((read.confidence ?? 0) * 100)}%</span>
                  </div>
                  <p className="text-muted">
                    {read.vehicle_class} · {read.camera_id}
                    {read.track_id != null ? ` · #${read.track_id}` : ""}
                    {alerts.some((alert) => alert.plate_norm === read.plate_norm) ? " · alert" : ""}
                  </p>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="flex-1 m-3 min-h-0 flex flex-col border border-border rounded-lg bg-surface-overlay overflow-hidden">
          <button type="button" className="w-full flex items-center gap-2 px-3 py-[var(--row-py)] text-left" onClick={toggleAlerts}>
            {alertsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <span className="text-label">Live alerts</span>
            <span className="ml-auto text-xs font-mono">{alerts.length}</span>
          </button>
          {!alertsOpen && topAlert && (
            <div className="px-3 pb-3 flex items-center gap-2 text-xs">
              <Badge tone={topAlert.severity === "high" || topAlert.severity === "critical" ? "danger" : "warning"}>
                {topAlert.severity}
              </Badge>
              <span className="font-mono text-accent">{topAlert.plate_norm}</span>
            </div>
          )}
          {alertsOpen && (
            <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-2">
              {alerts.length === 0 && <EmptyState icon={Bell} title="All clear" description="No active alerts" />}
              {alerts.map((alert) => {
                const severe = alert.severity === "high" || alert.severity === "critical";
                return (
                  <div
                    key={`${alert.id ?? alert.plate_norm}-${alert.ts}`}
                    className={`rounded border p-2 text-xs ${alert.severity === "critical" ? "card-critical" : severe ? "card-warning" : "border-border bg-surface-overlay"}`}
                  >
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span className="inline-flex items-center gap-1">
                        <AlertTypeIcon type={alert.type} className={severe ? "text-danger" : "text-warning"} />
                        <Badge tone={severe ? "danger" : "warning"}>{alert.severity}</Badge>
                      </span>
                      <span className="font-mono text-accent">{alert.plate_norm}</span>
                    </div>
                    <p className="text-slate-300">{alert.type.replace(/_/g, " ")}</p>
                    <p className="text-muted mt-1">{alert.ts ? <Timestamp iso={alert.ts} /> : "—"}</p>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </aside>
    </div>
  );
}
