"use client";

import { Maximize2, Minimize2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Kpi } from "@/components/ui/Kpi";
import { Timestamp } from "@/components/ui/Timestamp";
import { api } from "@/lib/api";
import { canAccessTrack } from "@/lib/auth";
import { playCue } from "@/lib/sounds";
import { openWallSocket } from "@/lib/ws";
import type { Camera, FrameNotice, FrameTrack, Role } from "@/types";

export function WallView() {
  const router = useRouter();
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [srcs, setSrcs] = useState<Record<string, string>>({});
  const [live, setLive] = useState<Record<string, boolean>>({});
  const [tracks, setTracks] = useState<Record<string, FrameTrack[]>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<Array<Record<string, unknown>>>([]);
  const timers = useRef<Record<string, number>>({});
  const lastRefresh = useRef<Record<string, number>>({});
  const pendingTracks = useRef<Record<string, FrameTrack[]>>({});
  const lastTickKey = useRef("");

  const refreshCamera = useCallback((cameraId: string, nextTracks: FrameTrack[]) => {
    pendingTracks.current[cameraId] = nextTracks;
    if (timers.current[cameraId]) return;
    const elapsed = Date.now() - (lastRefresh.current[cameraId] ?? 0);
    const wait = Math.max(0, 500 - elapsed);
    timers.current[cameraId] = window.setTimeout(() => {
      delete timers.current[cameraId];
      lastRefresh.current[cameraId] = Date.now();
      const tracksNow = pendingTracks.current[cameraId] ?? [];
      setTracks((prev) => ({ ...prev, [cameraId]: tracksNow }));
      setLive((prev) => ({ ...prev, [cameraId]: true }));
      api.cameraFrame(cameraId, Date.now()).then((frame) => {
        setSrcs((prev) => ({ ...prev, [cameraId]: frame.url }));
      }).catch(() => undefined);
    }, wait);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((user: { role?: Role } | null) => {
        if (!cancelled && user?.role) setRole(user.role);
      })
      .catch(() => undefined);

    api.cameras().then(async (cams) => {
      if (cancelled) return;
      setCameras(cams);
      const liveMap: Record<string, boolean> = {};
      const srcMap: Record<string, string> = {};
      await Promise.all(
        cams.map(async (cam) => {
          liveMap[cam.id] = Boolean(cam.has_live_frame);
          if (!cam.has_live_frame) return;
          try {
            const frame = await api.cameraFrame(cam.id, Date.now());
            srcMap[cam.id] = frame.url;
          } catch {
            liveMap[cam.id] = false;
          }
        }),
      );
      if (!cancelled) {
        setLive(liveMap);
        setSrcs(srcMap);
      }
    }).catch((err: unknown) => {
      if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load cameras");
    });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let socket: { close: () => void } | null = null;
    let off: () => void = () => undefined;
    let closed = false;

    openWallSocket().then((liveSocket) => {
      if (closed) {
        liveSocket.close();
        return;
      }
      socket = liveSocket;
      liveSocket.connect();
      off = liveSocket.onMessage((msg) => {
        if (msg.channel !== "frames") return;
        const data = msg.data as FrameNotice;
        if (!data?.camera_id) return;
        refreshCamera(data.camera_id, data.tracks ?? []);
      });
    }).catch((err: unknown) => {
      if (!closed) setError(err instanceof Error ? err.message : "Wall stream unavailable");
    });

    return () => {
      closed = true;
      off();
      socket?.close();
      Object.values(timers.current).forEach((id) => window.clearTimeout(id));
      timers.current = {};
    };
  }, [refreshCamera]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setExpanded(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!expanded) {
      setRecent([]);
      lastTickKey.current = "";
      return;
    }
    api.recentReads(expanded).then((res) => setRecent((res.reads ?? []).slice(0, 5))).catch(() => setRecent([]));
  }, [expanded]);

  useEffect(() => {
    if (!expanded) return;
    const tracksNow = tracks[expanded] ?? [];
    const key = tracksNow.map((track) => `${track.track_id}:${track.plate_norm}`).join("|");
    if (!key || key === lastTickKey.current) return;
    if (lastTickKey.current) playCue("plate-tick");
    lastTickKey.current = key;
  }, [tracks, expanded]);

  const allowTrack = role != null && canAccessTrack(role);
  const expandedCam = cameras.find((cam) => cam.id === expanded) ?? null;
  const liveCount = cameras.filter((cam) => live[cam.id] || cam.has_live_frame).length;
  const zoneName = (expandedCam as (Camera & { zone_name?: string; zone?: string }) | null)?.zone_name
    ?? (expandedCam as (Camera & { zone?: string }) | null)?.zone
    ?? "—";

  return (
    <div className="h-full overflow-auto p-4">
      <div className="flex items-end justify-between mb-4 gap-4">
        <div>
          <h1 className="text-sm font-semibold uppercase tracking-wide text-slate-200">Camera wall</h1>
          <div className="mt-2 w-48">
            <Kpi variant="hero" label="Live cameras" value={`${liveCount} / ${cameras.length || 0}`} />
          </div>
        </div>
      </div>
      {error && <p className="text-sm text-danger mb-3">{error}</p>}

      {expandedCam ? (
        <div className="flex gap-3 min-h-[70vh]">
          <div className="w-[70%] min-w-0 rounded-lg border border-border bg-surface-raised overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 border-b border-border">
              <h2 className="text-sm font-semibold">{expandedCam.name}</h2>
              <button
                type="button"
                aria-label="Exit focus"
                className="text-muted hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                onClick={() => setExpanded(null)}
              >
                <Minimize2 size={16} />
              </button>
            </div>
            <div className="relative">
              <ExpandedFrame
                src={srcs[expandedCam.id]}
                tracks={tracks[expandedCam.id] ?? []}
                allowTrack={allowTrack}
                onPlate={(plate) => router.push(`/track?plate=${encodeURIComponent(plate)}`)}
              />
              <PlateChip tracks={tracks[expandedCam.id] ?? []} allowTrack={allowTrack} />
            </div>
            <div className="p-3 text-sm space-y-2 border-t border-border">
              <p className="font-mono text-xs">{expandedCam.id} · {zoneName}</p>
              <p className="font-mono text-xs text-muted">{expandedCam.lat.toFixed(5)}, {expandedCam.lng.toFixed(5)}</p>
              <ul className="space-y-1">
                {recent.map((read, index) => (
                  <li key={index} className="flex justify-between text-xs font-mono">
                    <span>{String(read.plate_norm ?? "—")}</span>
                    <span className="text-muted">{read.ts ? <Timestamp iso={String(read.ts)} /> : ""}</span>
                  </li>
                ))}
              </ul>
              <Button size="sm" variant="secondary" onClick={() => router.push(`/live?focus=${encodeURIComponent(expandedCam.id)}`)}>
                Open on map
              </Button>
            </div>
          </div>
          <div className="w-[30%] overflow-y-auto space-y-2">
            {cameras.filter((cam) => cam.id !== expandedCam.id).map((cam) => (
              <CameraCell
                key={cam.id}
                cam={cam}
                src={srcs[cam.id]}
                live={Boolean(live[cam.id] || cam.has_live_frame)}
                tracks={tracks[cam.id] ?? []}
                allowTrack={allowTrack}
                compact
                onFocus={() => setExpanded(cam.id)}
              />
            ))}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {cameras.map((cam) => (
            <CameraCell
              key={cam.id}
              cam={cam}
              src={srcs[cam.id]}
              live={Boolean(live[cam.id] || cam.has_live_frame)}
              tracks={tracks[cam.id] ?? []}
              allowTrack={allowTrack}
              onFocus={() => setExpanded(cam.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function latestPlate(tracks: FrameTrack[]): FrameTrack | undefined {
  for (let index = tracks.length - 1; index >= 0; index -= 1) {
    if (tracks[index]?.plate_norm) return tracks[index];
  }
  return undefined;
}

function PlateChip({ tracks, allowTrack }: { tracks: FrameTrack[]; allowTrack: boolean }) {
  const track = latestPlate(tracks);
  if (!track?.plate_norm) return null;
  const label = `${track.plate_norm}  ${Math.round((track.confidence ?? 0) * 100)}%`;
  const className = "absolute top-8 right-2 z-10 bg-surface-overlay/80 font-mono text-sm p-1.5 rounded";
  if (!allowTrack) return <span className={className}>{label}</span>;
  return (
    <a
      href={`/track?plate=${encodeURIComponent(track.plate_norm)}`}
      target="_blank"
      rel="noreferrer"
      className={`${className} hover:text-accent`}
      onClick={(event) => event.stopPropagation()}
    >
      {label}
    </a>
  );
}

function CameraCell({
  cam,
  src,
  live,
  tracks,
  allowTrack,
  compact,
  onFocus,
}: {
  cam: Camera;
  src?: string;
  live: boolean;
  tracks: FrameTrack[];
  allowTrack: boolean;
  compact?: boolean;
  onFocus: () => void;
}) {
  return (
    <div className={`relative text-left rounded-lg border border-border bg-surface-raised overflow-hidden ${compact ? "h-[120px]" : ""}`}>
      <button type="button" onClick={onFocus} className="block w-full text-left">
        <div className={`relative bg-black ${compact ? "h-[88px]" : "aspect-video"}`}>
          {src ? (
            <img src={src} alt="" className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-xs text-muted">No frame</div>
          )}
          {live && (
            <span className="absolute top-2 left-2">
              <Badge tone="success">Live</Badge>
            </span>
          )}
        </div>
        {!compact && <div className="px-3 py-2 text-sm truncate">{cam.name}</div>}
      </button>
      <PlateChip tracks={tracks} allowTrack={allowTrack} />
      <button
        type="button"
        aria-label={`Focus ${cam.name}`}
        className="absolute top-2 right-2 z-10 rounded bg-surface/80 p-1 text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        onClick={onFocus}
      >
        <Maximize2 size={14} />
      </button>
    </div>
  );
}

function ExpandedFrame({
  src,
  tracks,
  allowTrack,
  onPlate,
}: {
  src?: string;
  tracks: FrameTrack[];
  allowTrack: boolean;
  onPlate: (plate: string) => void;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const paint = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !img.naturalWidth) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#4ade80";
    ctx.lineWidth = 2;
    for (const track of tracks) {
      if (!track.bbox || track.bbox.length < 4) continue;
      const [x1, y1, x2, y2] = track.bbox;
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    }
  }, [tracks]);

  useEffect(() => {
    paint();
  }, [paint, src]);

  function onClick(event: MouseEvent<HTMLCanvasElement>) {
    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!img || !canvas || !img.naturalWidth) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const x = ((event.clientX - rect.left) / rect.width) * img.naturalWidth;
    const y = ((event.clientY - rect.top) / rect.height) * img.naturalHeight;
    const hit = tracks.find((track) => {
      if (!track.plate_norm || !track.bbox || track.bbox.length < 4) return false;
      const [x1, y1, x2, y2] = track.bbox;
      const left = Math.min(x1, x2);
      const right = Math.max(x1, x2);
      const top = Math.min(y1, y2);
      const bottom = Math.max(y1, y2);
      return x >= left && x <= right && y >= top && y <= bottom;
    });
    if (!hit || !allowTrack) return;
    onPlate(hit.plate_norm);
  }

  return (
    <div className="relative bg-black">
      {src ? (
        <img ref={imgRef} src={src} alt="" className="w-full max-h-[50vh] object-contain" onLoad={paint} />
      ) : (
        <div className="aspect-video flex items-center justify-center text-sm text-muted">Waiting for a frame</div>
      )}
      <canvas
        ref={canvasRef}
        className={`absolute inset-0 w-full h-full ${allowTrack ? "cursor-crosshair" : "cursor-not-allowed"}`}
        title={allowTrack ? "Open trajectory" : "Analysts cannot open plate trajectories"}
        onClick={onClick}
      />
    </div>
  );
}
